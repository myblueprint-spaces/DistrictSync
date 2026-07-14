<!-- claugentic-dev-harness@0.3.0 managed — do not edit (copied from the claugentic-dev-harness plugin) -->
# NNNN — <Title>

- **Status:** Draft | In Review | Spec'd | Approved | Implementing | Done
- **Resumable from:** `<the exact next unchecked slice/box, or "awaiting user reply on §X">` — kept current as the plan evolves.
- **Blockers:** `<none | short list with expected resolution>`
- **Flags:** `<none>` — the running list of **reversible** judgment-calls the build flagged-and-continued (decision-gated autonomy: `docs/claugentic-WORKFLOW.md` → *Decision-gated autonomy*). One line each — *the call + the chosen default* (e.g. `named the helper selectItems (spec silent) — chose the audit convention`). Surfaced at the close as "things to review." **Distinct from Disposition** (this is reversible mid-run judgment to review async; Disposition is the done/defer/reject of *unbuilt* items) — not a parallel status model, just a list. An *irreversible* call is never a flag — it stops mid-run (class (c)).
- **Disposition at close:** the plan completes (and is deleted — git history keeps it) once every remaining unchecked item is **done** (checked) · **deferred** (a `docs/claugentic-ROADMAP.md` line, or — for a substantial / externally-blocked remainder — moved into a NEW plan + a roadmap line) · or **rejected** (a declined-decision line in `docs/claugentic-DECISIONS.md`). Gated only on the committed slice — never on deferred/rejected/blocked parts; never left lingering on an external blocker. (Source of truth: `docs/claugentic-WORKFLOW.md` → Plan file lifecycle.)
- **Roadmap item:** <link to docs/claugentic-ROADMAP.md entry>
- **References:** `docs/claugentic-ARCHITECTURE_TREE.md` · `docs/claugentic-DECISIONS.md` · related plans

## Problem
What's wrong / needed, and why it matters (cite `file:line`).

## Goals / Non-goals
- Goal: …
- Non-goal: … (explicitly out of scope — guards against creep)

## Approach
The chosen design and why; alternatives considered and rejected (1 line each).

## Architecture & holistic fit
The **initial architect pass** — frames how this item sits in the codebase/product and what to uphold, so architecture is set from the start (not discovered late) and guides every downstream agent. These are **initial thoughts / placeholders, NOT the deep per-slice Spec** (that stays just-in-time in build). **YAGNI guard:** initial framing of what to uphold — NOT a mandate to build every abstraction now; holistic ≠ gold-plated.

- **Codebase fit** — layering · module placement · design patterns · SOLID / separation-of-concerns (how this sits in the existing architecture).
- **Product fit** — how it serves the user / job-to-be-done (1–2 lines; point at `docs/claugentic-PRODUCT.md` for user-facing work).
- **Quality dimensions to uphold** — the relevant subset, **each mapped to its REAL `docs/claugentic-standards/` module** (e.g. maintainability/extensibility → `maintainability-structure` · performance → `performance-efficiency` · security → `security` · reliability → `reliability-resilience` · data → `data-and-persistence` · API → `api-and-contracts`) so the standards FRAME the plan from the start. This is the **forward-pointer to the Spec's "In-scope standards dimensions"** — name them here; the Spec refines them per slice (don't restate standards content, and don't fork a second dimension list).
- **Future-proofing** — what's likely to change; what to keep open *without building it now*.

_Substantial work fills this; trivial/lightweight changes may give it a one-liner or skip it (honor the effort-dial — no holistic pass on a typo fix). Model-upheld + `synthesizer-gate` (plan-gate)-audited; the template forces the section to exist, the reviewer audits it's genuinely reasoned._

## Affected files
`path` — what changes.

## Research / grounding
- **Files reviewed:** `file:line` breadcrumbs the author actually read.
- **Harness docs consulted:** which `docs/claugentic-standards/*` modules · `docs/claugentic-DECISIONS.md` · CLAUDE.md gotchas were read (list as read, not a coverage checkbox).
- **Findings:** what already exists to reuse · what gaps need building · what gotchas apply.

_Substantial plans fill this; small/local changes may skip it._

## Risks & mitigations
Risk → mitigation. Call out anything that could change existing behavior or output (e.g. a regression/snapshot test).

## Test strategy
What tests prove correctness + that existing behavior is preserved.

## Decomposition (slices)
Each slice must land **complete in one ≤1M-context session, no debt**.
- [ ] **Slice 1** — <scope> · lands complete because <…>
- [ ] **Slice 2** — …

---

## Review  _(filled by synthesizer-gate in its plan-gate altitude, Stage 3)_
- **Verdict:** PASS | CHANGES REQUIRED
- **Required changes:** …
- **Sizing/completeness:** per-slice OK / split …
- **Harness impact:** …

---

## Spec  _(per slice, after Review passes — Stage 4)_
### Slice 1
- **In plain English (shown first at the approval gate):** what this builds · what "done" means for you · what you're accepting (risks / trade-offs).
- **Files & changes:** signatures, exact edits.
- **In-scope standards dimensions:** <the docs/claugentic-standards/* modules this slice touches + target bar> (what Verify audits against).
- **Tests to add:** …
- **Acceptance criteria:** …
