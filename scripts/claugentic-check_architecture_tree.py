# claugentic-dev-harness@0.3.0 managed — do not edit (copied from the claugentic-dev-harness plugin)
#!/usr/bin/env python3
"""Enforce that docs/claugentic-ARCHITECTURE_TREE.md indexes every in-scope source file.

Deterministic gate (no LLM): checks PRESENCE (every in-scope file appears in the
tree), STALENESS (no tree entry points to a file that no longer exists), and GLOB
DRIFT (INCLUDE_GLOBS watches NO files while the repo nonetheless contains source —
the zero-coverage rot a wrong/unset glob would otherwise hide). Descriptions are
authored by humans/agents — this script does not write them. Drift DETECTION is
mechanical (the gate flags); resetting the globs is the agent's job, not the gate's.

In-scope = tracked + staged + **untracked-not-ignored** files matching the globs,
so a file just created via Write (not yet `git add`-ed) is caught immediately.

Fails loud: `_git` raises `RuntimeError` if git is missing or returns non-zero
(missing/erroring git or a non-repo cwd must NEVER read as a green "0 in-scope
files"). A returncode-0 with empty stdout is legitimate (empty repo / glob matches
nothing) and is left as an empty list. `main()` is the boundary that maps a git
failure to exit 1 (the only non-zero exit).

Modes:
    python scripts/claugentic-check_architecture_tree.py            # manual/CI: full scan, stdout, exit 1 on problems
    python scripts/claugentic-check_architecture_tree.py --staged    # pre-commit scope: gate the INDEX (tracked + staged),
                                                          # never unrelated untracked working-tree files

Run once per `git commit` by the `.githooks/pre-commit` wrapper (wired via
`core.hooksPath=.githooks`, which `init` configures for an adopter when the
architecture-tree gate is enabled); otherwise run manually / in CI. `init` decides
per scenario: FRESH (no tree, no source) and MATURE-NO-TREE (no tree, source present →
a cheap-complete backtick-prose skeleton) get the gate ON (pre-commit hook wired);
MATURE-WITH-TREE (an existing tree) is asked — Replace with a harness skeleton (gate ON)
or Keep-mine (gate OFF, INCLUDE_GLOBS=[], no hook wired). See CLAUDE.md ->
Harness Discipline.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

TREE_PATH = Path("docs/claugentic-ARCHITECTURE_TREE.md")
# Forward-slash rendering for user-facing messages (markdown/repo paths use `/`, and a
# stable form keeps the text identical across OSes — Path's __str__ would emit `\` on Windows).
TREE_DISPLAY = TREE_PATH.as_posix()

# ─────────────────────────────────────────────────────────────────────────────
# PER-REPO CONFIG — set by the `init` skill based on the repo's languages.
# ─────────────────────────────────────────────────────────────────────────────
# INCLUDE_GLOBS is the ONLY per-repo knob. It lists the files that MUST be indexed
# in claugentic-ARCHITECTURE_TREE.md. The `init` skill detects the target repo's
# languages/layout and writes the right globs here. They are passed to git as
# pathspecs; the `:(glob)` prefix gives true globstar (** spans directories, incl.
# zero).
#
# Entries MUST use EXTENSION globs (end in `*.<ext>`, e.g. `:(glob)src/**/*.ts`,
# `:(glob)cmd/**/*.go`) so the valid extensions are derivable (EXTS below) — that
# single source of truth drives the staleness check, with no second per-repo regex
# to keep in sync. An entry with no derivable `*.<ext>` (e.g. a bare directory glob
# like `:(glob)src/**`) is still PRESENCE-checked but is NOT staleness-checked
# (its files can't be told apart from any other path token in the tree's prose).
#
# This file is COPIED into adopter repos, so INCLUDE_GLOBS is per-repo: `init` rewrites it
# to match the adopter's own languages/layout (its source dirs, not these). The value
# below is the SOURCE repo's own (claugentic-dev-harness): the gate scripts plus the
# executable Workflow choreography under `engine/` (read-from-install-path, never copied
# to adopters — so this `engine/` widening is source-repo-only; init's body-compare already
# excludes the INCLUDE_GLOBS line on both sides, so no adopter REFRESH triggers). EXTS derives
# `js` automatically — every new in-scope file must be tree-indexed or CI goes red, the point.
INCLUDE_GLOBS = [":(glob)src/**/*.py", ":(glob)config/mappings/*.yaml"]

# Substrings that exempt a file (no architectural content).
EXCLUDE_SUBSTR = ("__pycache__", "/__init__.py")

# ─────────────────────────────────────────────────────────────────────────────
# GLOB-DRIFT DETECTION — a stack-agnostic, STABLE trip-wire (NOT a per-repo knob).
# ─────────────────────────────────────────────────────────────────────────────
# SOURCE_EXTS answers one question the per-repo INCLUDE_GLOBS deliberately can't:
# "does the repo contain source code at all?" — so the gate can flag the one
# zero-coverage failure where INCLUDE_GLOBS watches NOTHING while real code exists
# (init guessed globs on an empty repo, then the repo grew). It is intentionally
# broad + stable: file extensions don't drift the way per-stack tooling does, so
# there is no list to keep in lockstep with adopters' stacks.
#
# SCOPE — this is for DRIFT DETECTION ONLY. It is NOT used for presence/staleness;
# `INCLUDE_GLOBS` (and the `EXTS` derived from it) stay the ONLY per-repo knob there.
SOURCE_EXTS = frozenset(
    {
        "py", "js", "jsx", "mjs", "cjs", "ts", "tsx", "go", "rs", "java", "kt",
        "rb", "php", "cs", "swift", "c", "h", "cpp", "hpp", "cc", "scala",
        "vue", "svelte",
    }
)

# The managed-stamp token (the documented `/update` convention): a file the harness
# COPIED into an adopter repo carries `claugentic-dev-harness@<semver>` on its first
# line. Reused here so the copied gate script never false-trips drift on a day-0
# empty adopter repo (it's harness scaffolding, not the adopter's own source).
MANAGED_STAMP = "claugentic-dev-harness@"


def _exts_from_globs(globs: list[str]) -> set[str]:
    """Derive the set of valid extensions from INCLUDE_GLOBS (single source of truth).

    Parse the trailing `*.<ext>` of each glob and collect lowercase `<ext>`. Entries
    with no derivable `*.<ext>` (e.g. a bare directory glob) are skipped — those files
    stay presence-checked but not staleness-checked (see PER-REPO CONFIG above).
    """
    exts: set[str] = set()
    for glob in globs:
        match = re.search(r"\*\.(\w+)$", glob)
        if match:
            exts.add(match.group(1).lower())
    return exts


# Valid extensions for the staleness check, derived from INCLUDE_GLOBS (the only
# per-repo knob). Empty set ⇒ staleness is a no-op (extension-less globs only).
EXTS = _exts_from_globs(INCLUDE_GLOBS)

# Candidate path tokens inside the tree's markdown: backtick-quoted, path-shaped,
# carrying a dot-extension. Repo-agnostic (no per-repo tuning); the extension is
# then matched against EXTS, which is what makes a token an in-scope reference.
TOKEN_PATTERN = re.compile(r"`([\w./\\-]+\.\w+)`")

# Any single backtick-delimited token in the tree's markdown (the inline-code span),
# used for PRESENCE: a file is indexed iff its path appears as an EXACT backtick token,
# never as a raw substring (so a root `a.py` is NOT read as indexed merely because
# `scripts/a.py` appears somewhere, and a prose word in prose — no backticks — never
# counts as a path entry). The tree format already backticks every file path.
BACKTICK_TOKEN_PATTERN = re.compile(r"`([^`]+)`")

# Per-entry one-line FORM budget (configurable): the maximum characters a single index
# entry line may span. The tree's own header promises a "one-line-per-file index"; this
# is the deterministic ratchet that keeps entries one tight line and stops them rebloating
# into paragraphs (genuinely-useful mechanism detail belongs in the file's own header
# docstring — read on open — not in the index read every session). Form, NOT quality:
# the gate measures length, never judges the description's wording (that stays
# model-upheld + reviewer-caught). Tune this single constant to retune the budget.
MAX_ENTRY_CHARS = 450

# An index ENTRY line: a markdown list bullet (`^\s*- `) whose FIRST backtick-delimited
# token is PATH-SHAPED (contains `/` or `.`). Anchored on the backtick-bullet-with-path
# shape so the budget applies to file entries ONLY — NOT to "any list item" and NOT to
# "any long line": a `- `-bullet whose first backtick token is a plain word (no `/`/`.`)
# is prose, not an entry, and the two non-bullet prose lines (the top blurb, the
# eval-section intro) never match `^\s*- ` at all. Group 1 captures that first token so
# offenders can be reported by path.
ENTRY_LINE_PATTERN = re.compile(r"^\s*- `([^`]+)`")


def _strip_fenced_blocks(text: str) -> str:
    """Drop ```-fenced code/diagram blocks before any backtick tokenizing.

    A markdown fence (a line whose first non-space run is ```` ``` ````) opens a literal
    region; a real index ENTRY never lives inside one. Leaving fences in desyncs the
    sequential backtick-pair tokenizers (`BACKTICK_TOKEN_PATTERN` / `TOKEN_PATTERN` both
    `findall` non-overlapping): a fence's stray backticks flip pairing parity for every
    entry AFTER it, so correctly-formatted entries past an ASCII-diagram block read as
    MISSING and backticked tokens inside a diagram read as live references. This was the
    v0.1.26 adopter regression — a real adopter tree carrying ASCII directory diagrams in
    fences. Strip whole fenced regions line-wise; an unterminated fence strips to EOF
    (fail safe: under-tokenize a malformed tail rather than desync the whole document).
    """
    out: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(line)
    return "\n".join(out)


def _form_violations(text: str) -> list[tuple[str, int]]:
    """Entry lines OVER the `MAX_ENTRY_CHARS` one-line budget, as `(first-token-path, length)`.

    PRECONDITION: `text` is ALREADY `_strip_fenced_blocks`'d, so a ```-fenced ASCII-diagram
    line (which can legitimately be long) is exempt BY CONSTRUCTION — it was dropped before
    this ever sees it. The caller (`evaluate()`) passes the same stripped text it tokenizes.

    An ENTRY is a line matching `ENTRY_LINE_PATTERN` (a `^\\s*- `-bullet whose FIRST backtick
    token is PATH-SHAPED — contains `/` or `.`). The path-shaped guard is what excludes a
    `- `-bullet whose first backtick token is a plain word (e.g. `- `init` does X`): a plain
    word is prose, not a file entry, so its length is none of the gate's business. The two
    prose lines (the top blurb, the eval-section intro) never start with `- ` and so never
    match either. For each entry line whose TOTAL length exceeds the budget, record its first
    token (the file path, for the message) and the line's length. Pure: no I/O, no globals
    mutated — just text in, offenders out.
    """
    violations: list[tuple[str, int]] = []
    for line in text.splitlines():
        match = ENTRY_LINE_PATTERN.match(line)
        if match is None:
            continue
        token = match.group(1)
        # Path-shaped first token only: a `/` or `.` marks it as a file path, not a prose word.
        if "/" not in token and "." not in token:
            continue
        length = len(line)
        if length > MAX_ENTRY_CHARS:
            violations.append((token, length))
    return violations


def _backtick_tokens(text: str) -> set[str]:
    """All backtick-delimited tokens in `text`, normalized `\\`→`/` (the tree is markdown
    text; a Windows path may carry backslashes — mirror the FS-side `/`-normalization).
    Fenced blocks are stripped first (see `_strip_fenced_blocks`) so a diagram never
    desyncs the pairing.

    The single source of truth for "is this path an EXACT entry in the tree" — the
    presence check's view of what the tree already indexes.
    """
    return {t.replace("\\", "/") for t in BACKTICK_TOKEN_PATTERN.findall(_strip_fenced_blocks(text))}


def _git(*args: str) -> list[str]:
    """Run a git command, failing loud on genuine git failure.

    Raises `RuntimeError` if git is not installed (`FileNotFoundError`) or returns a
    non-zero exit code (errored / cwd is not a repository). A returncode-0 result with
    empty stdout is LEGITIMATE (empty repo, or a pathspec that matched nothing) and
    returns an empty list — only missing-git / non-zero is treated as a failure, so the
    gate can never silently read a broken git as a green "0 in-scope files".

    `-c core.quotepath=false` is prepended so git emits non-ASCII paths VERBATIM (UTF-8)
    instead of its default octal-escaped `"\\303\\251"` form — otherwise a file like
    `café.py` would never literal-match the tree text and read as perma-MISSING. We pair
    it with an explicit `encoding="utf-8"` so the bytes decode as UTF-8 on every platform,
    not via the host's locale codepage (cp1252 on Windows would mangle the same paths).
    """
    try:
        result = subprocess.run(
            ["git", "-c", "core.quotepath=false", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("git unavailable or not a repository: git executable not found") from exc
    except UnicodeDecodeError as exc:
        # Strict UTF-8 decode: a tracked filename whose bytes are not valid UTF-8 must land on
        # the same loud, controlled boundary as every other git failure (a UnicodeDecodeError is
        # a ValueError — without this re-raise it would bypass the RuntimeError handlers and
        # surface as a raw traceback instead of the controlled exit-1 error every caller relies on).
        raise RuntimeError(
            "git output was not valid UTF-8 — a tracked filename is not UTF-8-encoded; "
            f"rename that file or fix its encoding ({exc})"
        ) from exc
    if result.returncode != 0:
        raise RuntimeError(f"git unavailable or not a repository: {result.stderr.strip()}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _is_harness_managed(path: str) -> bool:
    """True if the managed stamp (the `/update` convention) is at the start of `path`.

    Reads a bounded 256-byte prefix — the stamp sits at byte 0 of line 1, so a fixed prefix
    is enough and stays hard-bounded even for a newline-less (minified) file. A read error
    (file vanished mid-scan, permission denied, path-is-a-directory — all `OSError`) returns
    False rather than crashing: drift detection must never blow up on an unreadable file, and
    treating it as un-managed is the safe (conservative) default — it then counts as source
    and errs toward FLAGGING drift, never toward a silent false all-clear.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return MANAGED_STAMP in fh.read(256)
    except OSError:
        return False


def _repo_source_files() -> list[str]:
    """Repo-wide source files (drift's view): committed + staged-new + untracked, SOURCE_EXTS only.

    The drift detector's "does this repo contain real code?" census. Two `_git` calls with NO
    pathspec, so it sees the WHOLE repo (unlike the glob-scoped in_scope_files()): `ls-files`
    lists the index — committed AND newly-`git add`-ed (staged-new) files — and `ls-files
    --others --exclude-standard` adds untracked-not-ignored. Normalized `\\`→`/`, kept only if
    the basename has a real `.<ext>` whose extension is in SOURCE_EXTS (so an extensionless file
    named `go`/`c`/`rs` is NOT misread as source), MINUS `EXCLUDE_SUBSTR` and MINUS harness-managed
    files (the copied gate script et al. — so a day-0 empty adopter repo isn't read as "has
    source"). Sorted. Fails loud via `_git`. Stamp reads happen only here, on the small surviving
    candidate set, and only when drift is actually being computed (zero-coverage state) — bounded.
    """
    tracked = _git("ls-files")
    untracked = _git("ls-files", "--others", "--exclude-standard")
    files = {f.replace("\\", "/") for f in (*tracked, *untracked)}
    candidates = sorted(
        f
        for f in files
        if "." in f.rsplit("/", 1)[-1]  # a real extension on the basename, not a dotless name
        and f.rsplit(".", 1)[-1].lower() in SOURCE_EXTS
        and not any(x in f for x in EXCLUDE_SUBSTR)
    )
    return [f for f in candidates if not _is_harness_managed(f)]


def glob_drift(in_scope: set[str]) -> list[str]:
    """Zero-coverage drift: a sample of un-watched source when INCLUDE_GLOBS sees NOTHING.

    Returns `[]` whenever the globs match ≥1 file — the steady state. The early return is
    also the load-bearing short-circuit: it fires BEFORE any `_repo_source_files()` call, so
    a healthy repo (this one) computes drift with zero stamp reads. Only when in_scope is
    empty (globs unset/`[]` or matching nothing) do we census the repo; a non-empty result
    (a small sample, capped) is the un-watched codebase the gate must flag.
    """
    if in_scope:
        return []
    return _repo_source_files()[:8]


def in_scope_files(include_untracked: bool = True) -> set[str]:
    """Tracked + staged (+ untracked-not-ignored unless `include_untracked` is False) files
    matching INCLUDE_GLOBS, minus exclusions.

    `include_untracked=False` is the **pre-commit (`--staged`) scope**: check only what is being
    committed — the index (tracked + staged-new) — NOT unrelated untracked source lying in the
    working tree, so an unstaged scratch file can't block an unrelated commit. The default `True`
    keeps the manual/CI scope (catch a file you forgot to `git add`)."""
    # Empty-globs guard: `git ls-files --` with NO pathspec lists EVERY file (a fail-open
    # bug — the gate would presence-check the whole repo). An unset INCLUDE_GLOBS means
    # "tracking not configured yet" → no in-scope files; drift (above) is what catches a
    # repo that has since grown real code.
    if not INCLUDE_GLOBS:
        return set()
    tracked = _git("ls-files", "--", *INCLUDE_GLOBS)
    staged = _git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "--", *INCLUDE_GLOBS)
    groups = [tracked, staged]
    if include_untracked:
        groups.append(_git("ls-files", "--others", "--exclude-standard", "--", *INCLUDE_GLOBS))
    files = {f.replace("\\", "/") for group in groups for f in group}
    return {f for f in files if not any(x in f for x in EXCLUDE_SUBSTR)}


def evaluate(include_untracked: bool = True) -> tuple[list[str], str]:
    """Return (problem_lines, success_summary). Empty problem_lines == OK.

    `include_untracked` threads to `in_scope_files`: False is the pre-commit (`--staged`) scope."""
    if not TREE_PATH.exists():
        return ([f"ERROR: {TREE_DISPLAY} is missing — create the architecture index."], "")
    # Strip ```-fenced blocks once: both the presence tokenizer (_backtick_tokens, which
    # re-strips defensively) and the staleness tokenizer (TOKEN_PATTERN.findall below) read
    # this text, and an index entry never lives inside a diagram fence.
    text = _strip_fenced_blocks(TREE_PATH.read_text(encoding="utf-8"))
    files = in_scope_files(include_untracked)
    # Presence: a file is indexed iff its path appears as an EXACT backtick-delimited
    # token — NOT a raw substring. The old `f not in text` false-green'd a root `a.py`
    # whenever a longer `scripts/a.py` appeared anywhere in the tree, and would have
    # counted a bare-prose mention as an entry. Whole-token equality kills both.
    entries = _backtick_tokens(text)
    missing = sorted(f for f in files if f not in entries)
    # Staleness: extract candidate tokens, normalize `\`→`/` (the tree is markdown
    # text; the FS may be Windows — mirror in_scope_files()'s normalization on this
    # side too), and keep only those whose last-dot extension is in EXTS. Whole-
    # extension equality structurally avoids the alternation bug (e.g. `ts` matching
    # inside `tsx`). No path-prefix filter — extension equality alone scopes it.
    candidates = (t.replace("\\", "/") for t in TOKEN_PATTERN.findall(text))
    referenced = {p for p in candidates if p.rsplit(".", 1)[-1].lower() in EXTS}
    stale = sorted(p for p in referenced if not Path(p).exists())
    # Glob drift: short-circuits on the non-empty `files` (steady state) BEFORE any repo
    # census. With INCLUDE_GLOBS == [] presence/staleness above are no-ops (files == set()),
    # but drift stays LIVE — so an unset repo that grows real code is still caught here.
    drift = glob_drift(files)
    # Form: entries over the one-line budget. Reads the SAME `_strip_fenced_blocks`'d `text`
    # as presence/staleness above, so fenced ASCII-diagram lines are exempt by construction.
    # Purely additive to `problems` — presence/staleness/drift are untouched.
    over_budget = _form_violations(text)

    problems: list[str] = []
    if missing:
        problems.append(f"{TREE_DISPLAY} is MISSING an entry for these files")
        problems.append("(add `- `<path>` — one-line description.` under the right section):")
        problems += [f"  + {f}" for f in missing]
    if stale:
        problems.append(f"{TREE_DISPLAY} references files that NO LONGER EXIST (remove/update):")
        problems += [f"  - {f}" for f in stale]
    if over_budget:
        problems.append(
            f"{TREE_DISPLAY} has entries OVER the one-line budget ({MAX_ENTRY_CHARS} chars) — "
            "distill each to a single line:"
        )
        problems += [f"  ! {path} — {n} chars" for path, n in over_budget]
    if drift:
        problems.append(
            f"INCLUDE_GLOBS watches no files, but the repo contains source code (e.g. `{drift[0]}`) — "
            "the globs are unset or stale; re-detect the layout and set INCLUDE_GLOBS in "
            "scripts/claugentic-check_architecture_tree.py to match the source files below:"
        )
        problems += [f"  ? {f}" for f in drift]
    return (problems, f"OK: {TREE_DISPLAY} indexes all {len(files)} in-scope files.")


def _repo_root() -> Path:
    """Repo root, derived from THIS script's location — never the process CWD, never hardcoded.

    Every path the gate touches (`TREE_PATH`, each `_git` call, the staleness `Path.exists()`)
    is repo-root-relative, but its callers launch it from ANY working directory — the
    `.githooks/pre-commit` hook (git runs it from the commit cwd) and manual/CI runs from a
    subdir. Anchoring to the script's own location makes the gate CWD-independent and portable
    across machines/adopters: the value is computed at runtime from `__file__`, never written
    down. Git is authoritative; when git is unavailable we fall back to `<script_dir>/..` (the
    script lives at `<repo>/scripts/`).
    """
    here = Path(__file__).resolve().parent
    # A git hook (e.g. the pre-commit gate) runs with GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE set in
    # the environment. Those OVERRIDE the `-C <here>` discovery, so `--show-toplevel` returns the
    # `-C` directory (the `scripts/` subdir) instead of the repo root — chdir'ing there hides every
    # repo-root-relative path and the tree reads as "missing" (observed in a linked git worktree).
    # Strip them for the discovery call so git walks UP from the script's own location, resolving
    # the correct root for a main OR a linked worktree. (The later `_git` index reads keep the
    # inherited env — there GIT_DIR/GIT_INDEX_FILE correctly point at what's being committed.)
    discover_env = {
        k: v for k, v in os.environ.items()
        if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")
    }
    try:
        out = subprocess.run(
            ["git", "-C", str(here), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            env=discover_env,
        )
        return Path(out.stdout.strip())
    except (FileNotFoundError, subprocess.CalledProcessError):
        return here.parent  # convention: the script lives at <repo>/scripts/


def _force_utf8_output() -> None:
    """Emit stdout/stderr as UTF-8 so non-ASCII glyphs in messages survive on Windows.

    Python encodes stdout/stderr with the host locale codepage by default (cp1252 on Windows),
    but the pre-commit wrapper captures the stream and prints it back as UTF-8 — so a lone
    em-dash (`—`, as in the "is missing —" message) mojibakes to `�` (a strict cp1252 console
    can even raise UnicodeEncodeError). Reconfiguring to UTF-8 fixes every message at once. A
    captured/replaced stream (pytest, a pipe wrapper) may lack `.reconfigure` → guarded,
    best-effort.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # Python 3.7+
        except (AttributeError, ValueError):
            pass


def main(argv: list[str]) -> int:
    # Boundary setup, run for EVERY mode: emit UTF-8 (Windows mojibake) and anchor to the repo
    # root so the gate is CWD-independent — the pre-commit hook and manual/CI runs can launch it
    # from any directory, and every path below is repo-root-relative.
    _force_utf8_output()
    os.chdir(_repo_root())
    # `--staged` = the pre-commit scope: check only the index (tracked + staged), never unrelated
    # untracked working-tree files, so an unstaged scratch file can't block an unrelated commit.
    staged = "--staged" in argv
    try:
        problems, summary = evaluate(include_untracked=not staged)
    except RuntimeError as exc:
        # The gate could not run — fail loud, never report a false green.
        print(f"ERROR: {exc}")
        return 1
    if problems:
        print("\n".join(problems) + f"\n\nUpdate {TREE_DISPLAY} with a one-line description (CLAUDE.md -> Harness Discipline).")
        return 1
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
