<!-- claugentic-dev-harness@0.1.40 managed — do not edit (copied from the claugentic-dev-harness plugin) -->
---
module: reliability-resilience
title: Reliability & Resilience
version: 0.1.0
status: draft
iso_25010: [reliability]
load_scope:
  keywords: [error, exception, retry, timeout, circuit-breaker, idempotent, concurrency, async, thread, race, deadlock, backpressure]
  globs: ["src/**"]
last_reviewed: 2026-06-04
---

# Reliability & Resilience — guard against failure, partial state, and contention

> **Loads when:** the change touches error handling, I/O with external systems, concurrent or async code, retries, timeouts, or shared mutable state.
> **ISO/IEC 25010:** reliability · **Status:** draft · **v0.1.0**

Each entry below is one **auditable dimension**. Per change, the reviewer applies the
*relevant* ones **fully** (select-don't-skip), right-sized to the change — never
gold-plating an irrelevant one, never skipping a relevant one.

---

## Correctness & failure paths

- **Good looks like —** Every edge and error path is explicitly handled; exceptions surface with actionable messages; no silent swallowing of errors; all operations that must succeed atomically are guarded so no partial state can persist.
- **Auditor checks —** Scan call sites for bare `except`/`catch` blocks that discard the exception or swallow it silently `[J]`; check that partial-state scenarios (e.g. write A succeeds, write B fails) leave the system in a consistent, recoverable state `[J]`; verify error messages identify the cause and suggest a remedy `[J]`.
- **Confidence —** `judgment` — requires a reviewer to trace failure paths through the logic; no gate can prove completeness.
- **Tradeoff (plain English) —** Explicit error handling makes failures visible and debuggable. The cost is more code and more test cases. Skipping it means silent data corruption or misleading success responses that are far harder to diagnose in production.
- **Sources —** "Fail loudly, fail fast" — Release It! (Michael Nygard, 2nd ed., §4); ENGINEERING_STANDARDS.md § Correctness & resilience.

---

## Idempotency & safe retry

- **Good looks like —** Mutating operations that cross a network boundary (API calls, queue publishes, DB writes) are idempotent or protected by an idempotency key, so retrying on transient failure cannot create duplicate side effects.
- **Auditor checks —** Identify every non-GET external call in the diff `[D via grep]`; check each for an idempotency key, deduplication guard, or "insert-or-ignore" / upsert semantics `[J]`; verify the client retry logic does not retry non-idempotent paths unconditionally `[J]`.
- **Confidence —** `judgment` — presence of a key is detectable `[D]`, but correctness of scope and implementation requires review.
- **Tradeoff (plain English) —** Idempotency lets you retry safely after a network hiccup without double-charging or double-creating records. It requires agreeing on a stable key (e.g. request ID) and storing deduplication state, which adds design overhead.
- **Sources —** "Designing for idempotency" — Stripe Engineering Blog (stripe.com/blog/idempotency); AWS Well-Architected Framework — Reliability Pillar, REL 9.

---

## Timeouts & retry with backoff

- **Good looks like —** Every blocking I/O call (HTTP, DB, queue) has an explicit timeout; retries use exponential backoff with jitter; maximum retry count is bounded.
- **Auditor checks —** Search the diff for HTTP client / DB connection instantiation and socket calls; verify a timeout parameter is set on each `[D via grep for `timeout`]`; verify retry loops include `sleep` / delay with a cap and a max-attempts guard `[J]`.
- **Confidence —** `judgment` — timeout presence is grep-able `[D]`, but whether the value is correct and whether backoff is implemented correctly is `[J]`.
- **Tradeoff (plain English) —** Without timeouts a hung downstream service stalls your threads indefinitely; without backoff a thundering-herd of retries amplifies an outage. The cost is slightly more complex call-site code and configuration to tune.
- **Sources —** Google SRE Book, Ch. 22 "Addressing Cascading Failures"; Release It! §5 "Timeouts".

---

## Circuit breakers & graceful degradation

- **Good looks like —** Where a dependency is non-critical or prone to outages, a circuit-breaker (or equivalent) prevents repeated failed calls from cascading; the system degrades gracefully (serves cached data, returns a safe default, or surfaces a clear partial-availability error) rather than propagating failure.
- **Auditor checks —** Identify external dependencies in the diff `[J]`; for each, check whether a circuit-breaker library or manual tripped-state guard exists for non-critical paths `[J]`; verify the degraded-mode response is intentional and documented `[J]`.
- **Confidence —** `judgment` — whether a dependency warrants a circuit-breaker is context-dependent; no gate can decide this.
- **Tradeoff (plain English) —** Circuit breakers add operational complexity (state to monitor, thresholds to tune). Skipping them means a single slow external service can exhaust your connection pool and take down the whole application.
- **Sources —** Release It! §5 "Circuit Breaker"; Martin Fowler, CircuitBreaker pattern (martinfowler.com/bliki/CircuitBreaker.html).

---

## Thread safety & concurrency hazards

- **Good looks like —** Shared mutable state is protected by locks, atomic primitives, or eliminated via immutability/message-passing; no race conditions, deadlocks, or starvation in async/threaded code; backpressure is applied at queue/channel boundaries to prevent unbounded growth.
- **Auditor checks —** Identify shared mutable state in the diff (class-level variables, singletons, module globals) `[J]`; verify access is serialized or the object is documented thread-local `[J]`; check async code for unguarded concurrent writes to shared collections `[J]`; verify channels/queues have bounded capacity or explicit backpressure `[J]`.
- **Confidence —** `judgment` — concurrency bugs require mental model tracing; no static gate reliably catches all races.
- **Tradeoff (plain English) —** Concurrency gives throughput but introduces non-deterministic failure modes that are hard to reproduce and debug. Explicit locking and immutability add overhead but make behavior predictable.
- **Sources —** Java Concurrency in Practice (Goetz et al.), §1; Python `asyncio` docs — "Synchronization Primitives"; ENGINEERING_STANDARDS.md § Resources & concurrency.

---

## Resource lifecycle & cleanup

- **Good looks like —** All acquired resources (file handles, DB connections, network sockets, threads) are released on both the happy path and every failure path; context managers / `try-finally` / RAII patterns are used so cleanup is guaranteed; memory is bounded (no unbounded accumulation in caches or queues).
- **Auditor checks —** Grep diff for `open(`, connection acquire, thread/process spawn calls `[D]`; verify each is wrapped in a context manager or explicit `finally` block `[J]`; check for in-memory collections that grow without a cap or eviction policy `[J]`.
- **Confidence —** `judgment` — structural presence of `with`/`finally` is grep-able `[D]`, but correctness of scope requires review.
- **Tradeoff (plain English) —** Resource leaks are invisible at small scale and catastrophic under load — connections pool exhausts, memory spikes, file descriptor limit hits. The fix (context managers) is cheap; the leak is expensive.
- **Sources —** PEP 343 — The "with" Statement; ENGINEERING_STANDARDS.md § Resources & concurrency.

---

## Authoring rules (the catalog meta-rules — do not delete)

- **Additive floor:** add dimensions as you discover them; **never delete** one. This catalog is meant to become "every standard we can think of."
- **Right-size:** apply only *relevant* dimensions per change (`KISS`/`YAGNI`); never skip a relevant one. Relevance is a per-change judgment — see `README.md`.
- **Novel patterns allowed** when they add clear value — justify (problem → why existing patterns fall short → benefit) and record in `claugentic-DECISIONS.md`. Unconventional ≠ wrong.
- **Every dimension carries a Confidence tag** so the harness can separate what it *proved* (deterministic gates) from what it *asserts* (judgment). Trust the oracle, not the model's word.
