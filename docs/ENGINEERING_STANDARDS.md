# Engineering Standards

A **project-agnostic, ever-growing catch-all** of engineering quality dimensions — the bar an implementation is held to. Reusable across any codebase that adopts this harness. `CLAUDE.md` and `docs/WORKFLOW.md` point here; the `implementer-architect` builds to it and the `architect-reviewer` audits against it.

## How to use this (meta-rules)

- **Select, don't skip.** For a given change, the architect picks the dimensions that are *relevant* and meets each one **fully** — no debt. Don't gold-plate irrelevant dimensions (that's its own waste — respect `KISS`/`YAGNI`), but **never skip a relevant one.** Relevance is a per-change judgment.
- **No hard "N/A" caps in the dimensions table.** Don't mark a dimension *permanently* irrelevant — a stack grows into things, and a cap would mislead a future agent. *Current* applicability for this repo lives in the **Current scope** section at the bottom as a **non-capping, growing snapshot**; ultimate relevance is always a per-change judgment.
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

---

## Current scope (this codebase — a living snapshot, **not a cap**)

This is the **one project-specific part** of this doc — everything above is universal. *When reusing this file in another project, keep the rest and rewrite just this section.* It records which dimensions are **LIVE in DistrictSync today** so agents know where to focus by default. **Guidance, not a gate:** relevance is still a per-change judgment (per the meta-rules), and this list **grows as the stack grows** — never use a `NOT-YET` here to skip a dimension genuinely relevant to a change.

**Stack today:** file-based ETL (GDE CSV/TXT → 5–7 CSVs), pandas, Pydantic YAML config; **no DB, no web API/server** (batch tool); SFTP egress + OS keyring; Streamlit UI; PyInstaller exe; `schtasks`/cron; **handles student PII**.

| Dimension | Scope now | In this codebase |
|---|---|---|
| Correctness & resilience | **LIVE** | encoding fallback, atomic writes+rollback, graceful skip; retries/backoff LIGHT (SFTP only) |
| Structure & design | **LIVE** | Strategy/registry, `_base` inheritance, Pydantic |
| DRY & reuse | **LIVE** | `column_names.py`, shared `BaseTransformer` |
| Performance & efficiency | **LIGHT** | pandas memory, kill O(n²), vectorize, memoize lookups · **DB/API tuning NOT-YET** (no DB/API) |
| Security | **LIVE** | keyring, host allowlist, `ALLOWED_TRANSFORMS`, scheduler-input validation, bandit · **user authn/authz NOT-YET** (no server) |
| Privacy & data governance | **LIVE — top priority** | student PII: no real data in repo, never logged, TLS via SFTP; FERPA-adjacent |
| Extensibility & maintainability | **LIVE** | config-driven core (`enabled_entities`) |
| API & interface design | **LIGHT** | contracts = output-CSV schema + YAML config schema (version those); no HTTP API |
| Observability & ops | **LIVE** | `__DISTRICTSYNC_RUN__` records, anomaly detection; no PII in logs |
| Resources & concurrency | **LIGHT** | context managers, temp-dir cleanup · concurrency NOT-YET (single-threaded; keep transformer singletons stateless) |
| Data integrity | **LIVE** | atomic writes, schema validation, orphaned-enrollment check, **active-roster referential integrity** (enrollments + homeroom classes filtered to `Students.csv` — plan 0003) |
| Internationalization | **LIGHT** | encoding fallback, date formats (DOB→ISO); timezones minimal |
| Configuration & deployment | **LIVE** | YAML + `~/.districtsync`, reproducible PyInstaller builds, GH Actions |
| Accessibility & UX | **LIGHT** | Streamlit UI only |
| Cost & efficiency | **NOT-YET** | district servers, not cloud-metered; watch memory on large GDEs |
| Testing | **LIVE** | 640 tests, SD74 snapshot regression, 80% gate |
| Docs & traceability | **LIVE** | `ARCHITECTURE_TREE.md`, `DECISIONS.md`, MkDocs |

**As the stack grows, promote rows here (and note it in `DECISIONS.md`):** add a DB → Performance(DB) + Data-integrity(transactions/migrations) go LIVE · add a web API/service → API design + Security(authn/authz, rate-limiting) + tracing go LIVE · add concurrency/threads/a queue → Resources & concurrency goes LIVE · move to metered cloud → Cost goes LIVE.
