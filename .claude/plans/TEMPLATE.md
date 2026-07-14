# NNNN — <Title>

- **Status:** Draft | In Review | Spec'd | Approved | Implementing | Done
- **Roadmap item:** <link to docs/ROADMAP.md entry>
- **References:** `docs/ARCHITECTURE_TREE.md` · `docs/DECISIONS.md` · related plans

## Problem
What's wrong / needed, and why it matters (cite `file:line`).

## Goals / Non-goals
- Goal: …
- Non-goal: … (explicitly out of scope — guards against creep)

## Approach
The chosen design and why; alternatives considered and rejected (1 line each).

## Affected files
`path` — what changes.

## Risks & mitigations
Risk → mitigation. Call out anything that could change existing output (e.g. SD74 snapshot).

## Test strategy
What tests prove correctness + that existing behavior is preserved.

## Decomposition (slices)
Each slice must land **complete in one ≤1M-context session, no debt**.
- [ ] **Slice 1** — <scope> · lands complete because <…>
- [ ] **Slice 2** — …

---

## Review  _(filled by plan-reviewer, Stage 3)_
- **Verdict:** PASS | CHANGES REQUIRED
- **Required changes:** …
- **Sizing/completeness:** per-slice OK / split …
- **Harness impact:** …

---

## Spec  _(per slice, after Review passes — Stage 4)_
### Slice 1
- **Files & changes:** signatures, exact edits.
- **Tests to add:** …
- **Acceptance criteria:** …
