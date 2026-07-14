<!-- claugentic-dev-harness@0.3.0 managed — do not edit (copied from the claugentic-dev-harness plugin) -->
# Engineering Standards

The engineering quality bar is a **modular catalog** under [`docs/claugentic-standards/`](claugentic-standards/README.md) — scoped modules loaded only when relevant, anchored to **ISO/IEC 25010:2023**. This file is the thin entry point.

**Start here → [`docs/claugentic-standards/README.md`](claugentic-standards/README.md)** — the catalog index, the meta-rules (select-don't-skip, additive, novel-patterns-allowed), the two-tier global/local model, and versioning. The module contract is [`docs/claugentic-standards/_TEMPLATE.md`](claugentic-standards/_TEMPLATE.md).

**How it plugs into the workflow:** the spec (Stage 4) names the in-scope modules/dimensions; `implementer` builds to them; `synthesizer-gate` audits against them; "done" = the in-scope dimensions pass (see `docs/claugentic-WORKFLOW.md` → Definition of Done).

> Capability modules (Redis, queues, storage, …) are authored just-in-time, when a change pulls one in.

---

> **Current scope (per repo).** This file is a **managed copy**, so the per-repo scope does **not** live here. The `init` skill seeds it in the adopter repo's **`CLAUDE.md` `harness:` section** (a local, non-managed spot): a living, non-capping snapshot of which dimensions/modules are LIVE in *that* repo today (relevance is always a per-change judgment; it grows as the stack grows). The plugin ships only the universal catalog and does **not** ship a populated Current scope.
