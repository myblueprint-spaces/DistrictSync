#!/usr/bin/env python3
"""Enforce that docs/ARCHITECTURE_TREE.md indexes every in-scope source file.

Deterministic gate (no LLM): checks PRESENCE (every in-scope file appears in the
tree) and STALENESS (no tree entry points to a file that no longer exists).
Descriptions are authored by humans/agents — this script does not write them.

In-scope = tracked + staged + **untracked-not-ignored** files matching the globs,
so a file just created via Write (not yet `git add`-ed) is caught immediately.

Usage:
    python scripts/check_architecture_tree.py          # human/CI: stdout, exit 1 on problems
    python scripts/check_architecture_tree.py --hook    # Claude Code hook: silent on success;
                                                        # on problems, stderr + exit 2 (nudges/blocks the agent)

Wired in `.claude/settings.json` as a PostToolUse(Write) nudge + a Stop backstop;
also runnable via `make check-tree` and in CI. See CLAUDE.md -> Harness Discipline.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

TREE_PATH = Path("docs/ARCHITECTURE_TREE.md")

# Files that MUST be indexed. Passed to git as pathspecs; the :(glob) prefix gives
# true globstar (** spans directories incl. zero). Tune to taste.
INCLUDE_GLOBS = [":(glob)src/**/*.py", ":(glob)config/mappings/*.yaml"]

# Substrings that exempt a file (no architectural content).
EXCLUDE_SUBSTR = ("__pycache__", "/__init__.py")

# Path shapes the staleness check recognizes inside the tree's text.
STALE_PATTERN = re.compile(r"(src/[\w./-]+\.py|config/mappings/[\w./-]+\.ya?ml)")


def _git(*args: str) -> list[str]:
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def in_scope_files() -> set[str]:
    """Tracked + staged + untracked-not-ignored files matching INCLUDE_GLOBS, minus exclusions."""
    tracked = _git("ls-files", "--", *INCLUDE_GLOBS)
    staged = _git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "--", *INCLUDE_GLOBS)
    untracked = _git("ls-files", "--others", "--exclude-standard", "--", *INCLUDE_GLOBS)
    files = {f.replace("\\", "/") for f in (*tracked, *staged, *untracked)}
    return {f for f in files if not any(x in f for x in EXCLUDE_SUBSTR)}


def evaluate() -> tuple[list[str], str]:
    """Return (problem_lines, success_summary). Empty problem_lines == OK."""
    if not TREE_PATH.exists():
        return ([f"ERROR: {TREE_PATH} is missing — create the architecture index."], "")
    text = TREE_PATH.read_text(encoding="utf-8")
    files = in_scope_files()
    missing = sorted(f for f in files if f not in text)
    referenced = set(STALE_PATTERN.findall(text))
    stale = sorted(p for p in referenced if not Path(p).exists())

    problems: list[str] = []
    if missing:
        problems.append("docs/ARCHITECTURE_TREE.md is MISSING an entry for these files")
        problems.append("(add `- `<path>` — one-line description.` under the right section):")
        problems += [f"  + {f}" for f in missing]
    if stale:
        problems.append("docs/ARCHITECTURE_TREE.md references files that NO LONGER EXIST (remove/update):")
        problems += [f"  - {f}" for f in stale]
    return (problems, f"OK: docs/ARCHITECTURE_TREE.md indexes all {len(files)} in-scope files.")


def main(argv: list[str]) -> int:
    hook_mode = "--hook" in argv
    problems, summary = evaluate()
    if problems:
        msg = "\n".join(problems) + "\n\nUpdate docs/ARCHITECTURE_TREE.md with a one-line description (CLAUDE.md -> Harness Discipline)."
        if hook_mode:
            print(msg, file=sys.stderr)  # fed back to the agent; exit 2 = blocking
            return 2
        print(msg)
        return 1
    if not hook_mode:
        print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
