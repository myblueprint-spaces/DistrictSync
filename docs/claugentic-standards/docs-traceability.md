<!-- claugentic-dev-harness@0.2.4 managed — do not edit (copied from the claugentic-dev-harness plugin) -->
---
module: docs-traceability
title: Docs & Traceability
version: 0.1.1
status: draft
iso_25010: [maintainability]
load_scope:
  keywords: [docs, readme, comment, docstring, adr, architecture-tree]
  globs: ["docs/**", "**/*.md"]
last_reviewed: 2026-06-22
---

# Docs & Traceability — the change is explainable, the architecture is navigable

> **Loads when:** changes add, move, or remove files (ARCHITECTURE_TREE.md); introduce non-trivial decisions (DECISIONS.md); modify public APIs or non-obvious logic (docstrings/comments); or touch onboarding/runbook documentation.
> **ISO/IEC 25010:** maintainability · **Status:** draft · **v0.1.1**

Each entry below is one **auditable dimension**. Per change, the reviewer applies the
*relevant* ones **fully** (select-don't-skip), right-sized to the change — never
gold-plating an irrelevant one, never skipping a relevant one.

---

## Architecture-tree index currency

- **Good looks like —** `docs/claugentic-ARCHITECTURE_TREE.md` reflects the actual file layout with a one-line description per file. Every file add, move, or delete within scope triggers an update to the tree in the same commit.
- **Auditor checks —** If the architecture-tree gate is wired, run it (`python` / `python3` / `py` — `scripts/claugentic-check_architecture_tree.py`) and confirm exit 0 `[D]`; otherwise verify the tree by eye `[J]`. Either way, confirm any new file added in this change has a description entry `[J]`.
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** A current ARCHITECTURE_TREE means a new agent (or team member) can navigate the codebase without reading every file; the cost is updating one line per file change. A stale tree wastes agent context and misdirects exploration.
- **Sources —** the claugentic-dev-harness architecture-tree discipline (a first-class harness rule); Grady Booch "Object-Oriented Analysis and Design" on the value of navigable architecture documentation.

---

## Decision traceability (DECISIONS.md)

- **Good looks like —** Every non-trivial choice (library selection, pattern choice, schema decision, API contract) is recorded as a dated one-liner in `docs/claugentic-DECISIONS.md` in the same commit that introduces the decision. Future agents consult it before re-litigating a past choice.
- **Auditor checks —** Review the diff for non-trivial decisions not yet recorded `[J]`; confirm `claugentic-DECISIONS.md` entry is dated and includes the rationale, not just the choice `[J]`.
- **Confidence —** `judgment` — what counts as "non-trivial" is a reviewer call.
- **Tradeoff (plain English) —** A decisions log prevents the same debate from happening three times with three different outcomes; it costs 30 seconds per decision. Without it, future agents re-open closed decisions and introduce inconsistency.
- **Sources —** Michael Nygard "Documenting Architecture Decisions" (https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions) — the original ADR essay; CLAUDE.md harness discipline.

---

## Load-bearing invariant traceability (INVARIANTS.md)

- **Good looks like —** A constraint that *must stay true or something breaks* — and whose rationale is non-obvious from the code — is recorded in `docs/claugentic-INVARIANTS.md` as a standing entry: the **invariant** (what must hold), the **why** (the rationale / blast radius if violated), and **dated provenance** (when, and what failure or near-miss, motivated it). The file is **lazily created** — it exists only once a repo has its first load-bearing invariant to record (an empty repo has none, and that is correct). It is **user-owned documentation, not a gate**: nothing mechanically verifies the invariants hold — the value is that the *next* change near a constraint reads *why before touching it*. Distinct from `docs/claugentic-DECISIONS.md` (a historical "what we chose and why," read when revisiting a choice): an invariant is **live** — read every time code in its blast radius changes.
- **Auditor checks —** `[J]` Did this change establish a non-obvious constraint that future code could silently violate (an ordering dependency, a "these two values must move together," an assumption a caller relies on) — and if so, is it captured in `docs/claugentic-INVARIANTS.md` with its why + dated provenance? `[J]` Did this change *touch the blast radius of an existing recorded invariant* — and if so, does it still hold (and is the entry still accurate)? `[J]` Is each entry genuinely load-bearing (a real "or it breaks"), not a restatement of a style preference or a decision that belongs in `claugentic-DECISIONS.md`?
- **Confidence —** `judgment` — there is no gate; whether a constraint is load-bearing, and whether a change threatens one, is a reviewer call. (Deliberately ungated: a stale or missing invariant entry is a documentation gap, not a build failure — wiring a check here would over-engineer a doc into machinery.)
- **Tradeoff (plain English) —** Writing down the handful of "this must stay true or X breaks" rules — with the story of the failure that taught you each one — means the next person (or agent) reads the landmine *before* stepping on it, instead of re-discovering it in production. The cost is a few lines per genuine invariant; the file stays tiny because most code carries none. Over-record it and it becomes noise nobody trusts — only truly load-bearing constraints earn an entry.
- **Sources —** the claugentic-dev-harness invariants discipline (independently converged on by multiple adopter projects); D. Parnas, "On the Criteria To Be Used in Decomposing Systems into Modules" (the assumptions a module's clients rely on are exactly its load-bearing invariants); M. Nygard, "Documenting Architecture Decisions" (the sibling ADR practice this complements).
- **Motivating incident —** load-bearing constraints (e.g. the two version manifests that must move together) were caught only by eye or in production, because the "or it breaks" rationale lived in nobody's head but the original author's; the next change near the constraint re-discovered the landmine instead of reading it. Multiple adopter projects independently started a constraints log for exactly this.

---

## Docstrings and inline comments

- **Good looks like —** Public APIs, non-obvious algorithms, and "why not the obvious approach" reasoning carry docstrings or inline comments. Comments explain *why*, not *what* (the code says what). Trivial getters and self-evident code are not commented (noise reduction).
- **Auditor checks —** Confirm public functions/classes have docstrings `[D]` (enforced by lint where available); flag complex or counterintuitive logic that has no explanatory comment `[J]`; flag comments that merely restate the code `[J]`.
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Good docstrings let the next developer understand intent without running a debugger; the cost is a few extra lines. "Clean code reads like prose" is aspirational — reality has edge cases worth narrating. Over-commenting creates noise that ages badly.
- **Sources —** Robert C. Martin "Clean Code" ch. 4 "Comments"; Google Python Style Guide §3.8 "Comments and Docstrings" (https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings).

---

## Onboarding and runbook documentation

- **Good looks like —** A new developer can clone and run the project by following `docs/SETUP.md` without asking anyone. Operational procedures (deploy, rollback, incident response, cron management) have a runbook reference. The README explains the project's purpose and entry points.
- **Auditor checks —** If setup steps changed, confirm `docs/SETUP.md` is updated in this commit `[J]`; verify any new operational procedure (cron, migration, flag toggle) has a runbook reference `[J]`.
- **Confidence —** `judgment` — completeness of onboarding docs is a reviewer call.
- **Tradeoff (plain English) —** Current setup docs cut onboarding from days to hours and enable incident response without the original author present; the cost is updating docs alongside the code change. Stale setup docs are worse than none — they actively mislead.
- **Sources —** Thoughtworks "Documentation" in "Building Microservices" (Sam Newman); Google SRE Book ch. 32 "The Evolving SRE Engagement Model" on runbook quality.

---

## Change explainability (commit and PR narrative)

- **Good looks like —** Commits follow Conventional Commits style (`feat:`, `fix:`, `chore:`, etc.) and the message explains *why*, not just *what*. PRs include a summary, test plan, and link to the relevant spec/issue. The change can be understood from its git history without reading the code.
- **Auditor checks —** Confirm commit messages are conventional and explain motivation `[J]`; verify PR description covers what changed, why, and how to test it `[J]`.
- **Confidence —** `judgment` — message quality is a reviewer call.
- **Tradeoff (plain English) —** Good commit messages make `git blame` a first-class debugging tool and turn code review into a narrative rather than a puzzle; the cost is two extra sentences per commit. A repo with poor commit history forces every future change to reverse-engineer intent from code alone.
- **Sources —** Conventional Commits specification v1.0.0 (https://www.conventionalcommits.org/); Chris Beams "How to Write a Git Commit Message" (https://cbea.ms/git-commit/).

---

## Authoring rules (the catalog meta-rules — do not delete)

- **Additive floor:** add dimensions as you discover them; **never delete** one. This catalog is meant to become "every standard we can think of."
- **Right-size:** apply only *relevant* dimensions per change (`KISS`/`YAGNI`); never skip a relevant one. Relevance is a per-change judgment — see `README.md`.
- **Novel patterns allowed** when they add clear value — justify (problem → why existing patterns fall short → benefit) and record in `claugentic-DECISIONS.md`. Unconventional ≠ wrong.
- **Every dimension carries a Confidence tag** so the harness can separate what it *proved* (deterministic gates) from what it *asserts* (judgment). Trust the oracle, not the model's word.
