"""One bundled-config count, spelled once (plan 0053 S13a).

``tests/_pins.py`` owns ``BUNDLED_CONFIG_COUNT``. This file holds the two things that keep it
the ONE spelling:

* **the lockstep copies outside ``tests/``** — ``.github/workflows/ci.yml``'s
  ``EXPECTED_CONFIGS`` (read from the parsed YAML, never grepped from the raw text), the
  Makefile's ``validate-config`` list (its length AND its set, against discovery) and
  the four prose count sentences — two in CLAUDE.md, two in
  ``docs/developer/configuration-reference.md`` since plan 0053 S15 moved the roster and
  ``district_domains`` paragraphs there — each with a doctored-copy twin;
* **a scan of ``tests/``** for a numeric config-count literal anywhere but ``_pins.py``
  (``len(...) == 20``, ``EXPECTED_CONFIGS = 20``), with a reasoned allowlist for the
  look-alikes that count something else. Every allowlist entry must still be FOUND in the
  real tree — that is the scan's non-vacuity proof.

The count pin itself (discovery == the constant, and its monkeypatched-to-19 twin) lives
in ``tests/test_config_version_gate.py`` beside the derived ``ALL_BUNDLED_CONFIGS``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml

from src.config.loader import available_configs
from tests._pins import BUNDLED_CONFIG_COUNT

_REPO = Path(__file__).resolve().parents[1]
_TESTS = _REPO / "tests"
_CI = _REPO / ".github" / "workflows" / "ci.yml"
_MAKEFILE = _REPO / "Makefile"
_CLAUDE_MD = _REPO / "CLAUDE.md"
#: Plan 0053 S15 moved CLAUDE.md's bundled-config roster and ``district_domains`` paragraphs
#: here VERBATIM, and two of the four count sentences moved with them.
_CONFIG_REFERENCE = _REPO / "docs" / "developer" / "configuration-reference.md"
_BUNDLED = _REPO / "config" / "mappings"

_CI_STEP = "Validate all mapping configs"
_CI_LITERAL = re.compile(r"^\s*EXPECTED_CONFIGS\s*=\s*(\d+)\s*$", re.MULTILINE)
_MAKE_LIST = re.compile(r"^validate-config:\n\t.*?for n in \[([^\]]*)\]", re.MULTILINE)

#: The prose sentences that state the count, each with the ONE doc it lives in. Each must be
#: present exactly once in its home and ABSENT from the other doc here: a copy left behind in
#: CLAUDE.md (or pasted back into it) would be a second spelling no row reads.
_COUNT_SENTENCES: tuple[tuple[Path, re.Pattern[str]], ...] = (
    (_CLAUDE_MD, re.compile(r"validates all (\d+) configs")),
    (_CONFIG_REFERENCE, re.compile(r"Total: (\d+) bundled configs")),
    (_CONFIG_REFERENCE, re.compile(r"pinned (\d+)-config count")),
    (_CLAUDE_MD, re.compile(r"pinned at (\d+)")),
)
_COUNT_DOCS: tuple[Path, ...] = (_CLAUDE_MD, _CONFIG_REFERENCE)


# --------------------------------------------------------------------------- #
# The lockstep copies outside tests/                                          #
# --------------------------------------------------------------------------- #
def _ci_expected(text: str) -> int | None:
    """``EXPECTED_CONFIGS`` from the named step of the PARSED workflow (``None`` if absent)."""
    workflow = yaml.safe_load(text)
    for job in workflow.get("jobs", {}).values():
        for step in job.get("steps", []):
            if step.get("name") == _CI_STEP:
                match = _CI_LITERAL.search(step.get("run", ""))
                return int(match.group(1)) if match else None
    return None


def _make_names(text: str) -> list[str] | None:
    match = _MAKE_LIST.search(text)
    if match is None:
        return None
    return [name.strip().strip("'\"") for name in match.group(1).split(",") if name.strip()]


def _count_doc_texts() -> dict[Path, str]:
    return {doc: doc.read_text(encoding="utf-8") for doc in _COUNT_DOCS}


def _sentence_counts(texts: dict[Path, str]) -> list[int | None]:
    """The count each sentence states in its home doc: ``None`` unless the sentence appears
    exactly once there AND nowhere else among ``texts``."""
    counts: list[int | None] = []
    for home, pattern in _COUNT_SENTENCES:
        found = {doc: pattern.findall(text.replace("**", "")) for doc, text in texts.items()}
        elsewhere = any(found[doc] for doc in texts if doc != home)
        counts.append(int(found[home][0]) if len(found[home]) == 1 and not elsewhere else None)
    return counts


class TestTheLockstepCopies:
    def test_ci_yml_expected_configs_equals_the_pin(self) -> None:
        assert _ci_expected(_CI.read_text(encoding="utf-8")) == BUNDLED_CONFIG_COUNT

    def test_twin_a_doctored_ci_literal_is_red(self) -> None:
        text = _CI.read_text(encoding="utf-8")
        doctored = text.replace(f"EXPECTED_CONFIGS = {BUNDLED_CONFIG_COUNT}", "EXPECTED_CONFIGS = 19", 1)
        assert doctored != text, "non-vacuity: the literal was found in the raw workflow"
        assert _ci_expected(doctored) == 19

    def test_twin_a_renamed_ci_step_reads_as_absent_not_as_a_match(self) -> None:
        doctored = _CI.read_text(encoding="utf-8").replace(f"name: {_CI_STEP}", "name: Something else", 1)
        assert _ci_expected(doctored) is None

    def test_the_makefile_validates_exactly_the_bundled_set(self) -> None:
        names = _make_names(_MAKEFILE.read_text(encoding="utf-8"))
        assert names is not None, "the validate-config recipe's list was not found"
        assert len(names) == BUNDLED_CONFIG_COUNT
        assert sorted(names) == available_configs(_BUNDLED)

    def test_twin_a_makefile_missing_a_config_is_red(self) -> None:
        text = _MAKEFILE.read_text(encoding="utf-8")
        doctored = text.replace("'unitychristianmyedbc',", "", 1)
        assert doctored != text
        names = _make_names(doctored)
        assert names is not None and len(names) == BUNDLED_CONFIG_COUNT - 1

    def test_each_count_sentence_states_the_pin_in_its_one_home(self) -> None:
        counts = _sentence_counts(_count_doc_texts())
        assert counts == [BUNDLED_CONFIG_COUNT] * len(_COUNT_SENTENCES), counts

    def test_twin_a_stale_count_sentence_is_red(self) -> None:
        """The shape S13a fixed in place: the picker-order note said "19-config" (now in the
        configuration reference, where S15 moved it)."""
        texts = _count_doc_texts()
        text = texts[_CONFIG_REFERENCE]
        doctored = text.replace(f"pinned {BUNDLED_CONFIG_COUNT}-config count", "pinned 19-config count", 1)
        assert doctored != text
        assert 19 in _sentence_counts({**texts, _CONFIG_REFERENCE: doctored})

    def test_twin_a_stale_claude_md_count_is_red(self) -> None:
        texts = _count_doc_texts()
        text = texts[_CLAUDE_MD]
        doctored = text.replace(f"pinned at {BUNDLED_CONFIG_COUNT}", "pinned at 19", 1)
        assert doctored != text
        assert 19 in _sentence_counts({**texts, _CLAUDE_MD: doctored})

    def test_twin_a_moved_sentence_copied_back_into_claude_md_is_red(self) -> None:
        """S15's own failure mode: a sentence that moved out, pasted back, is a second spelling."""
        texts = _count_doc_texts()
        doctored = texts[_CLAUDE_MD] + f"\nTotal: {BUNDLED_CONFIG_COUNT} bundled configs.\n"
        assert _sentence_counts({**texts, _CLAUDE_MD: doctored})[1] is None


# --------------------------------------------------------------------------- #
# No numeric config-count literal in tests/ outside _pins.py                  #
# --------------------------------------------------------------------------- #
#: The window a stale literal would sit in: the count, one fewer (a deleted config) and
#: one more (the shipped set plus a user overlay).
_WINDOW = frozenset({BUNDLED_CONFIG_COUNT - 1, BUNDLED_CONFIG_COUNT, BUNDLED_CONFIG_COUNT + 1})
_COUNT_NAME = re.compile(r"(?i)config|district|catalog|bundled")

#: Look-alikes that count something ELSE, keyed on (file, the unparsed expression).
_ALLOWED: dict[tuple[str, str], str] = {
    ("test_failure_policy_parity.py", "len(sites) >= 20"): "§5 catalogue site numbers, not configs",
    ("test_ui_flet_config_editor.py", "len(note) > 20"): "a note's character length, not configs",
}


def _is_len_call(node: ast.expr) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "len"


def _in_window(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and type(node.value) is int and node.value in _WINDOW


def _count_literals(source: str) -> list[str]:
    """Every ``len(...) <op> N`` and every ``<count-ish name> = N`` with ``N`` in the window."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            if any(_is_len_call(o) for o in operands) and any(_in_window(o) for o in operands):
                found.append(ast.unparse(node))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None and _in_window(node.value):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and _COUNT_NAME.search(t.id) for t in targets):
                found.append(ast.unparse(node))
    return found


def _tree_findings() -> set[tuple[str, str]]:
    files = [p for p in sorted(_TESTS.rglob("*.py")) if "__pycache__" not in p.parts and p.name != "_pins.py"]
    assert len(files) > 100, "non-vacuity: the test tree was found"
    return {(path.name, expr) for path in files for expr in _count_literals(path.read_text(encoding="utf-8"))}


class TestNoConfigCountLiteralOutsideThePins:
    def test_no_numeric_config_count_literal_outside_pins(self) -> None:
        offenders = sorted(finding for finding in _tree_findings() if finding not in _ALLOWED)
        assert offenders == [], f"import BUNDLED_CONFIG_COUNT from tests/_pins.py instead: {offenders}"

    def test_every_allowlist_entry_is_live(self) -> None:
        """Non-vacuity on the REAL tree: the scan finds each allowlisted look-alike."""
        assert set(_ALLOWED) <= _tree_findings()

    def test_detector_flags_the_retired_shapes(self) -> None:
        """Built from the constant, so the self-test moves with ``_WINDOW`` on a bump."""
        n = BUNDLED_CONFIG_COUNT
        expected = [
            f"len(ids) == {n}",
            f"len(rows) == {n + 1}",
            f"{n - 1} == len(names)",
            f"EXPECTED_CONFIGS = {n}",
            f"bundled_count: int = {n}",
        ]
        synthetic = (
            f"assert len(ids) == {n}\n"
            f"assert len(rows) == {n + 1}\n"
            f"assert {n - 1} == len(names)\n"
            f"EXPECTED_CONFIGS = {n}\n"
            f"bundled_count: int = {n}\n"
        )
        assert sorted(_count_literals(synthetic)) == sorted(expected)

    def test_detector_passes_the_constant_and_unrelated_numbers(self) -> None:
        clean = (
            "assert len(ids) == BUNDLED_CONFIG_COUNT\n"
            "assert len(rows) == BUNDLED_CONFIG_COUNT + 1\n"
            "assert len(rows) == 3\n"
            "threshold = 20\n"
            "assert value == 20\n"
        )
        assert _count_literals(clean) == []
