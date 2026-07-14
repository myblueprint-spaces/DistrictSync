<!-- claugentic-dev-harness@0.1.40 managed — do not edit (copied from the claugentic-dev-harness plugin) -->
---
module: performance-efficiency
title: Performance & Efficiency
version: 0.1.0
status: draft
iso_25010: [performance-efficiency]
load_scope:
  keywords: [performance, latency, throughput, cache, n+1, complexity, memory, streaming, cost, polling]
  globs: ["src/**"]
last_reviewed: 2026-06-04
---

# Performance & Efficiency — do the right amount of work, at the right cost

> **Loads when:** the change touches queries, caching, loops, pagination, resource sizing, streaming, or pay-per-use infrastructure.
> **ISO/IEC 25010:** performance-efficiency · **Status:** draft · **v0.1.0**

Each entry below is one **auditable dimension**. Per change, the reviewer applies the
*relevant* ones **fully** (select-don't-skip), right-sized to the change — never
gold-plating an irrelevant one, never skipping a relevant one.

---

## Algorithmic complexity

- **Good looks like —** Hot paths have the minimum feasible complexity; O(n²) or worse loops over large data sets are replaced with O(n log n) or O(n) alternatives; complexity is justified with a comment when it cannot be reduced.
- **Auditor checks —** Identify loops over collections in the diff `[J]`; flag any nested loop over the same unbounded collection as a candidate for algorithmic improvement `[J]`; check whether sorting/searching uses an appropriate data structure (set, dict, heap) rather than linear scan `[J]`.
- **Confidence —** `judgment` — no gate determines "large enough to matter"; requires reviewer's knowledge of realistic data volumes.
- **Tradeoff (plain English) —** A quadratic algorithm works fine at 100 rows and silently degrades at 10 000. Fixing it requires understanding the problem; skipping it hides a time-bomb that only appears at production scale.
- **Sources —** Introduction to Algorithms (CLRS), 4th ed., Ch. 3 "Growth of Functions"; ENGINEERING_STANDARDS.md § Performance & efficiency.

---

## Caching & invalidation

- **Good looks like —** Frequently read, rarely changing data is cached with an explicit TTL or event-driven invalidation strategy; cache keys are collision-safe; stale-reads are bounded and documented; cache stampede (thundering herd on cold cache) is mitigated.
- **Auditor checks —** Identify cache reads/writes in the diff `[D via grep for cache-related identifiers]`; verify every cache write sets a TTL or has a documented invalidation trigger `[J]`; check that cache keys include all discriminating parameters `[J]`; flag cache-aside patterns without stampede protection on hot keys `[J]`.
- **Confidence —** `judgment` — TTL presence is grep-able `[D]`; correctness of strategy and key design requires review.
- **Tradeoff (plain English) —** Caching cuts latency and DB load dramatically. Incorrect invalidation serves stale data, and a stampede on cold cache can spike the origin harder than no cache at all.
- **Sources —** "Caching Best Practices" — AWS Architecture Blog; Martin Fowler, Cache-Aside pattern (martinfowler.com/patterns); ENGINEERING_STANDARDS.md § Performance & efficiency.

---

## Database access patterns

- **Good looks like —** No N+1 query patterns; queries are paginated; only required columns are selected; indexes cover the WHERE/JOIN columns of frequent queries; bulk operations use batch inserts/updates; connection pooling is configured.
- **Auditor checks —** Scan ORM usage for lazy-loaded relationships accessed inside a loop `[J]`; verify list endpoints use `.limit()` / `.offset()` or cursor pagination `[J]`; grep for `SELECT *` and flag `[D]`; check migration files for index additions on FK/filter columns `[J]`; verify connection pool settings are not left at defaults for production load `[J]`.
- **Confidence —** `judgment` — `SELECT *` is grep-detectable `[D]`; N+1 detection and index adequacy require schema context.
- **Tradeoff (plain English) —** A missing index or N+1 loop is invisible at dev-scale and catastrophic in production — a single endpoint can saturate the DB. Fixing it post-launch requires a prod migration window; fixing it at PR time is free.
- **Sources —** "Use the index, Luke" (use-the-index-luke.com); Django ORM optimization guide; ENGINEERING_STANDARDS.md § Performance & efficiency.

---

## API & network efficiency

- **Good looks like —** API consumers use pagination for large result sets; request batching replaces chatty loops; payloads include only needed fields; retry logic uses exponential backoff (see reliability-resilience module); polling is replaced with webhooks or push where feasible.
- **Auditor checks —** Identify API calls in loops in the diff `[J]`; flag missing pagination on list endpoints that could return unbounded results `[J]`; check whether a polling loop could be replaced by a webhook/event subscription `[J]`; verify response payloads are not over-fetching (GraphQL over-select, REST extra fields) `[J]`.
- **Confidence —** `judgment` — all checks require knowledge of the upstream API contract and data volumes.
- **Tradeoff (plain English) —** Chatty APIs and unbounded list calls burn bandwidth, inflate latency, and trigger rate limits. Pagination and batching are a small design investment that prevents outages and unexpected API bills.
- **Sources —** "API Design Guide" — Google Cloud (cloud.google.com/apis/design); ENGINEERING_STANDARDS.md § Performance & efficiency.

---

## Memory & streaming

- **Good looks like —** Large datasets are streamed or paginated rather than loaded entirely into memory; generators/iterators are preferred over materialised lists for pipelines; image/file processing uses chunked reads; memory footprint of the change is considered for the expected concurrency level.
- **Auditor checks —** Flag code that loads an entire DB result set or file into a list/array in one call `[J]`; verify file processing uses a streaming API or chunk loop `[J]`; check that large in-memory collections are bounded or short-lived `[J]`.
- **Confidence —** `judgment` — "large" is context-dependent; no gate can determine expected data volume.
- **Tradeoff (plain English) —** Loading everything into memory is simple to write and fatal under load — a single large request OOMs the process. Streaming is slightly more complex but keeps memory flat regardless of data size.
- **Sources —** Python docs — "Generators" (PEP 255); Node.js Streams guide (nodejs.org/en/docs/guides); ENGINEERING_STANDARDS.md § Performance & efficiency.

---

## Cost & resource efficiency

- **Good looks like —** Compute, memory, storage, and egress resources are right-sized for the workload; wasteful polling is replaced with event-driven triggers; pay-per-call APIs (LLMs, vision, SMS) are called only when necessary and are not called inside tight loops; resource usage is visible (logged or metered).
- **Auditor checks —** Identify calls to pay-per-use APIs in the diff `[J]`; flag any such call inside a loop or bulk processor without a batching/dedupe guard `[J]`; check for polling intervals that could be replaced by a webhook or queue consumer `[J]`; verify that resource allocations (instance sizes, memory limits) are not hardcoded at "maximum safe" when a lower tier suffices `[J]`.
- **Confidence —** `judgment` — cost optimality requires knowledge of billing model and load profile; no gate can decide this.
- **Tradeoff (plain English) —** Over-provisioning feels safe but wastes money; unnecessary API calls to metered services accumulate silently until the invoice arrives. A small review step prevents both.
- **Sources —** AWS Well-Architected Framework — Cost Optimization Pillar; ENGINEERING_STANDARDS.md § Cost & efficiency.

---

## Authoring rules (the catalog meta-rules — do not delete)

- **Additive floor:** add dimensions as you discover them; **never delete** one. This catalog is meant to become "every standard we can think of."
- **Right-size:** apply only *relevant* dimensions per change (`KISS`/`YAGNI`); never skip a relevant one. Relevance is a per-change judgment — see `README.md`.
- **Novel patterns allowed** when they add clear value — justify (problem → why existing patterns fall short → benefit) and record in `claugentic-DECISIONS.md`. Unconventional ≠ wrong.
- **Every dimension carries a Confidence tag** so the harness can separate what it *proved* (deterministic gates) from what it *asserts* (judgment). Trust the oracle, not the model's word.
