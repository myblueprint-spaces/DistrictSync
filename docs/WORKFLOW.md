# Agent Development Workflow

How agents **and** human devs take *substantial* work from idea → landed change while keeping quality high and the harness self-improving. CLAUDE.md links here; this is the source of truth for process.

> **One-line:** Triage → Discuss → Plan → Review the plan → Spec → **Approve** → Implement → Verify → Land → **Retrospect**. Small changes skip to Implement+Verify.

---

## 0. Triage — does this need the full pipeline?

**Full pipeline** for *substantial* work: a new subsystem, a cross-cutting refactor, a change to a shared contract/pattern/standard, a security boundary, or anything touching more than ~3 files or a pattern documented in CLAUDE.md / STANDARDS.

**Lightweight path** for small/local/mechanical changes: go straight to **Implement → Verify**, still updating `ARCHITECTURE_TREE.md` and `DECISIONS.md` as needed.

When unsure, default to full; the plan-reviewer (Stage 3) confirms the path was right.

---

## Principles (apply at every stage)

- **Slice small, land complete.** Every unit of work must be finishable by **one specialist agent in a single ≤1M-token-context session** and land **vertically complete** — no half-done state, no `TODO`/debt left behind. If it doesn't fit, decompose further *before* implementing. This is a hard gate, not a guideline.
- **No new tech debt.** A landed slice leaves the codebase at least as clean as it found it: tests added, docs/ARCHITECTURE_TREE updated, no dead code, no silenced errors.
- **The harness is living.** Any task may improve STANDARDS / CLAUDE.md / the `.claude/agents/` role library / this workflow. Stage 9 is how that happens; treat harness improvements as first-class output, not a chore.
- Plus the CLAUDE.md non-negotiables: SOLID > DRY > KISS > YAGNI · fail loudly · validate at boundaries · configurable-over-hardcoded · single source of truth.

---

## Roles — a library, not a fixed pair

The orchestrator **selects the role(s) that fit the task** and may spawn several or compose them. It is not locked to a fixed sequence of agents.

Starter library (`.claude/agents/`):
- **`plan-reviewer`** — adversarially critiques a plan (correctness, SOLID/patterns, risk, **sizing & completeness**, over-engineering/YAGNI, harness impact) and writes findings back into the plan file.
- **`implementer-architect`** — implements an approved spec to standard, in an isolated worktree, landing one slice complete.

Also available without new files: built-in **`Explore`** (fan-out search), **`Plan`** (drafting), and any `code-reviewer` agent for diff review. **Add new role files as needs emerge** — the library is meant to grow (Stage 9).

---

## The pipeline

| # | Stage | Owner | Output |
|---|-------|-------|--------|
| 0 | **Triage** | orchestrator | full vs lightweight path |
| 1 | **Discuss & brainstorm** | orchestrator + **user** | crystal-clear scope; tangents→ROADMAP, decisions→DECISIONS |
| 2 | **Draft plan** | orchestrator / `Plan` | `.claude/plans/NNNN-<slug>.md` from `TEMPLATE.md`, **sliced into ≤1-session units** |
| 3 | **Review the plan** | `plan-reviewer` (+ others as fit) | critique written into the plan's *Review* section; iterate until it passes the gate |
| 4 | **Spec** | orchestrator | plan upgraded to implementation-ready spec **per slice**: file-by-file changes, signatures, test list, acceptance criteria |
| 5 | **Approval gate** | **user** | sign-off on the spec — *no code before this* |
| 6 | **Implement** | `implementer-architect` | one slice/session, isolated worktree/branch; upholds CLAUDE.md; updates ARCHITECTURE_TREE inline |
| 7 | **Verify** | implementer + a reviewer | full tests **+ SD74 snapshot + `check-tree` + ruff/mypy/bandit** green; review pass confirms spec match |
| 8 | **Land & archive** | orchestrator | conventional commit/PR; move plan → `docs/archive/<year>/`; append DECISIONS |
| 9 | **Retrospect & evolve** | orchestrator | harvest learnings into the harness (see below) |

**Stage 3 gate — a plan may not pass review until:** it is correct & sound (SOLID/patterns), each slice is **session-sized and lands complete with no debt**, the right path was chosen, risks + test strategy are stated, and any harness impact (new STANDARD/agent/doc) is noted. The reviewer writes a verdict + required changes into the plan; the orchestrator iterates.

---

## 9. The learning loop (how the harness grows)

After a slice lands, **harvest** before moving on:

- A convention that recurred across review findings → promote to **STANDARDS / CLAUDE.md**.
- A prompt tweak that made a specialist sharper → fold into the **`.claude/agents/` role file**.
- Friction in the process itself → edit **this `WORKFLOW.md`**.
- A notably clean implementation → record it as the **reference pattern** to copy (promotion rule).
- Every non-trivial choice → one dated line in **`DECISIONS.md`**.

Periodically run a **consolidation pass** (merge duplicates, prune stale guidance, keep the index lean). The intent: each task starts smarter than the last. Feedback flows *upstream* from any stage — a plan-reviewer finding, an implementer surprise, a verification failure can all become a permanent harness improvement.

```
task → DECISIONS/ROADMAP → (periodic) consolidation → STANDARDS · CLAUDE.md · agents · WORKFLOW updated → next task starts smarter ↺
```

---

## Plan file lifecycle

`​.claude/plans/NNNN-<slug>.md` (active, contains Plan + Review + Spec + slice checklist) → on completion, move to `docs/archive/<year>/`. One plan per substantial change; the slices inside it are the per-session units. Numbering is sequential (`0001`, `0002`, …).
