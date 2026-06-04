# Engineering Standards

A **project-agnostic, ever-growing catch-all** of engineering quality dimensions — the bar an implementation is held to. Reusable across any codebase that adopts this harness. `CLAUDE.md` and `docs/WORKFLOW.md` point here; the `implementer-architect` builds to it and the `architect-reviewer` audits against it.

## How to use this (meta-rules)

- **Select, don't skip.** For a given change, the architect picks the dimensions that are *relevant* and meets each one **fully** — no debt. Don't gold-plate irrelevant dimensions (that's its own waste — respect `KISS`/`YAGNI`), but **never skip a relevant one.** Relevance is a per-change judgment.
- **No "N/A here" annotations.** Do **not** mark a dimension irrelevant *to a codebase* in this file — a stack can grow into it later, and a permanent cap would mislead a future agent. Decide relevance at the moment of the change, not in the doc.
- **Additive, not subtractive.** You may **add** dimensions/standards as you discover them; **don't remove** existing ones. This is meant to become "every standard we can think of."
- **Not confined — to this list or to known patterns.** Exceed the list when a change warrants it. Prefer established design patterns, but you **may invent a novel pattern** when it adds clear value — justify the problem, why existing patterns fall short, and the benefit, and record it in `DECISIONS.md`. Unconventional ≠ wrong.
- **The spec names the in-scope dimensions.** Stage 4 records which dimensions apply to a slice and the target bar; Stage 7 audits against them; "done" = they pass (see **Definition of Done** in `WORKFLOW.md`).

## The dimensions

| Dimension | What "done right" includes |
|---|---|
| **Correctness & resilience** | edge/error paths; **fail loudly** (no swallowed exceptions); idempotency & safe-retry; timeouts + retry-with-backoff on I/O; circuit-breakers where apt; graceful degradation; atomic ops (no partial state) |
| **Structure & design** | SOLID; separation of concerns; the *right* pattern (Strategy/Factory/Adapter/Repository/Observer/…) **or a justified novel one**; composition > inheritance; dependency direction (DIP); small cohesive units; make invalid states unrepresentable (types) |
| **DRY & reuse** | reuse existing components before writing new; single source of truth (config/types/constants); extract shared logic; prefer proven libraries over reinvention |
| **Performance & efficiency** | algorithmic complexity (kill needless O(n²)); **caching + invalidation**; **DB** (N+1, indexes, batching, pooling, pagination, avoid `SELECT *`); **API** (batching, throttling, backoff, pagination, payload size); streaming vs load-all; vectorization; lazy loading; *profile before optimizing* |
| **Security** | authn/authz + least privilege; **secrets management** (none hardcoded, none in logs; vault/keyring/env); injection prevention + input sanitization (SQL/command/path/XSS); CSRF/SSRF where apt; safe deserialization (no eval/exec on untrusted); dependency/supply-chain hygiene (pin + scan); secure defaults |
| **Privacy & data governance** | PII minimization/anonymization; encrypt in transit & at rest; **never commit or log real user data**; retention & deletion policy; consent; **compliance** (FERPA/GDPR/HIPAA/PCI as applicable); audit trails |
| **Extensibility & maintainability** | Open/Closed + config-driven; clear contracts/interfaces; typed; backward-compat & **versioning** of contracts; readable naming; docstrings for non-obvious; low coupling / high cohesion |
| **API & interface design** | consistent, minimal, documented contracts; idempotency; pagination; versioning; rate-limit/backpressure; clear error shapes; stable public surface |
| **Observability & ops** | structured logging (levels, **no secrets/PII**); actionable error messages; metrics/run-records; tracing where apt; health & anomaly checks; alerting hooks |
| **Resources & concurrency** | close handles/connections (context managers); bounded memory; cleanup-on-failure; thread-safety/statelessness for shared state; race conditions; deadlocks; backpressure |
| **Data integrity** | transactions/atomic writes; schema validation at boundaries; referential integrity; idempotent writes; forward/backward-compatible migrations |
| **Internationalization** | encoding (UTF-8); locale; **timezones & date/number formats**; translatable strings; no locale-dependent parsing bugs |
| **Configuration & deployment** | 12-factor config; env separation; no env-specific hardcoding; feature flags / progressive rollout; reproducible builds; safe rollback |
| **Accessibility & UX** | (for UIs) a11y (WCAG), keyboard nav, contrast; clear error/empty/loading states; responsive |
| **Cost & efficiency** | compute/memory/storage budgets; avoid wasteful polling; right-size resources; mind egress/throughput cost |
| **Testing** | unit + integration + **regression/snapshot**; **failure-path & edge-case** tests; determinism; coverage of new behavior; no flaky tests |
| **Docs & traceability** | `ARCHITECTURE_TREE.md` updated; `DECISIONS.md` appended; docstrings; onboarding; the change is explainable |

> This table is a **floor that grows**, not a ceiling. Add a row when you discover a standard worth keeping; don't delete rows.

## Plugs into the workflow

- **Spec (Stage 4):** name the in-scope dimensions + target bar for the slice.
- **Implement (Stage 6):** `implementer-architect` builds to those dimensions the first time (and may justify a novel pattern).
- **Verify (Stage 7):** `architect-reviewer` audits the diff against the in-scope dimensions — performant, secure, efficient, extensible.
- **Definition of Done:** acceptance criteria met **+** in-scope dimensions pass **+** all gates green **+** **no new tech debt.** Iterate to meet this *fixed* bar, then stop. (Genuinely separate future work → `ROADMAP.md` — that's backlog, not debt.)
