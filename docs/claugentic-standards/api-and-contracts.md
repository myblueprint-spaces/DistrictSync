<!-- claugentic-dev-harness@0.1.40 managed — do not edit (copied from the claugentic-dev-harness plugin) -->
---
module: api-and-contracts
title: API & Interface Design
version: 0.1.0
status: draft
iso_25010: [compatibility]
load_scope:
  keywords: [api, endpoint, route, contract, version, pagination, rate-limit, webhook]
  globs: ["**/api/**", "**/routes/**", "**/controllers/**"]
last_reviewed: 2026-06-04
---

# API & Interface Design — consistent, minimal, stable public surfaces

> **Loads when:** the change adds or modifies API endpoints, routes, controllers, public function signatures, webhooks, or any cross-boundary contract.
> **ISO/IEC 25010:** compatibility · **Status:** draft · **v0.1.0**

Each entry below is one **auditable dimension**. Per change, the reviewer applies the
*relevant* ones **fully** (select-don't-skip), right-sized to the change — never
gold-plating an irrelevant one, never skipping a relevant one.

---

## Minimal & consistent contracts

- **Good looks like —** Endpoints expose only what callers need (no over-fetching, no internal implementation leaking through); naming conventions (casing, pluralisation, verb/noun split) are consistent across the surface; request/response shapes follow the project's schema conventions.
- **Auditor checks —** Scan new endpoints for fields that belong to internal domain models but have no external consumer `[J]`; check that path segments, query-param names, and JSON keys follow the project's established casing convention `[D via lint/schema tool if configured, otherwise J]`; verify HTTP verbs are used semantically (GET = safe+idempotent, POST = create, PUT/PATCH = update, DELETE = remove) `[J]`.
- **Confidence —** `judgment` — consistency is a cross-endpoint concern that requires reviewing the whole surface, not just the diff.
- **Tradeoff (plain English) —** A consistent contract is learnable in minutes; an inconsistent one creates indefinite support burden. Leaking internals through the API couples consumers to implementation details that you then cannot change.
- **Sources —** "API Design Guide" — Google Cloud (cloud.google.com/apis/design); RESTful Web APIs (Richardson & Amundsen), Ch. 1.

---

## Idempotency of mutating endpoints

- **Good looks like —** POST/PUT/DELETE endpoints that create or modify resources accept an idempotency key or are naturally idempotent by design (upsert semantics, content-addressed writes); retrying the same request with the same key produces the same observable result.
- **Auditor checks —** Identify mutating endpoints in the diff `[D via HTTP verb]`; verify each either documents idempotency naturally or accepts an `Idempotency-Key` / equivalent header and deduplicates on it `[J]`; check the deduplication store has an appropriate TTL `[J]`.
- **Confidence —** `judgment` — presence of a key header is detectable `[D]`; correctness of deduplication logic requires review.
- **Tradeoff (plain English) —** Without idempotency, a network timeout leaves the client unable to safely retry — it either double-submits or gives up. Adding idempotency keys costs a small amount of storage and a deduplication check per request.
- **Sources —** Stripe API Reference — Idempotent Requests (stripe.com/docs/api/idempotent_requests); ENGINEERING_STANDARDS.md § API & interface design.

---

## Versioning & backward compatibility

- **Good looks like —** Breaking changes (field removals, type changes, semantic changes) are gated behind a new API version; additive changes (new optional fields, new endpoints) are backward-compatible and do not require a version bump; the versioning scheme (URL path, header, or query param) is consistent and documented.
- **Auditor checks —** Identify any removed or renamed request/response fields in the diff `[J]`; verify the change is either backward-compatible (field is new and optional) or introduced under a new version path `[J]`; check that the version scheme is consistent with existing endpoints `[J]`; confirm deprecated fields carry a documented sunset date if applicable `[J]`.
- **Confidence —** `judgment` — whether a change is "breaking" requires understanding existing consumers; no automated gate can determine this without a full contract-testing suite.
- **Tradeoff (plain English) —** Unversioned breaking changes silently break downstream consumers at deploy time. Versioning adds routing complexity but gives consumers a migration window and protects you from on-call incidents.
- **Sources —** "API Versioning" — Stripe Engineering Blog; Semantic Versioning 2.0 (semver.org); ENGINEERING_STANDARDS.md § Extensibility & maintainability.

---

## Pagination & bounded responses

- **Good looks like —** Every list/collection endpoint returns a bounded page of results with a cursor or offset/limit; response includes a `next` link or `total` count so clients can paginate; default page size is documented and reasonable (e.g. 20–100); callers cannot request an unbounded result set.
- **Auditor checks —** Identify list endpoints in the diff `[D via route glob]`; verify each applies `.limit()` / slice / cursor before returning `[J]`; check that the API schema documents the pagination contract (fields, max page size) `[J]`; flag endpoints where `limit` is accepted from the caller but has no server-side cap `[J]`.
- **Confidence —** `judgment` — presence of limit in query logic is grep-able `[D]`; whether the cap is enforced server-side requires review.
- **Tradeoff (plain English) —** An unbounded list endpoint is a denial-of-service vector and a latency landmine — one bad query can load millions of rows. Pagination is a small design constraint that protects both the server and the consumer.
- **Sources —** "Pagination" — Google Cloud API Design Guide; ENGINEERING_STANDARDS.md § Performance & efficiency.

---

## Rate limiting & backpressure

- **Good looks like —** Public or high-traffic endpoints declare a rate limit; the limit is enforced server-side (not just documented); responses include standard rate-limit headers (`X-RateLimit-Limit`, `X-RateLimit-Remaining`, `Retry-After`); clients receive `429 Too Many Requests` and a retry hint rather than an opaque error.
- **Auditor checks —** Identify new public-facing endpoints in the diff `[J]`; verify rate-limiting middleware or decorator is applied `[J]`; check that `429` responses include a `Retry-After` header `[J]`; confirm the limit is configured externally (not hardcoded) `[J]`.
- **Confidence —** `judgment` — middleware presence is structurally checkable `[J]`; correct header values and limit logic require review.
- **Tradeoff (plain English) —** Without rate limiting, a misbehaving or malicious client can starve legitimate traffic. Adding it requires deciding on a limit and communicating it to consumers, but protects availability for everyone.
- **Sources —** IETF RFC 6585 § 4 "429 Too Many Requests"; "Rate Limiting" — Stripe Engineering Blog; ENGINEERING_STANDARDS.md § API & interface design.

---

## Clear & stable error shapes

- **Good looks like —** Error responses follow a single schema across all endpoints (e.g. `{ "error": { "code": "...", "message": "...", "details": [...] } }`); error codes are machine-readable and stable across versions; HTTP status codes are semantically correct (400 for client errors, 5xx for server errors); internal stack traces and implementation details are never exposed to the caller.
- **Auditor checks —** Scan new error-return paths in the diff for consistency with the project's error envelope schema `[J]`; check that 5xx responses do not leak stack traces or internal paths in the response body `[J]`; verify status codes are semantically appropriate for the error condition `[J]`; confirm error codes are documented `[J]`.
- **Confidence —** `judgment` — schema shape is inspectable `[J]`; stack-trace leakage is detectable by integration test or manual review `[J]`.
- **Tradeoff (plain English) —** Inconsistent error shapes force every API consumer to write bespoke error-parsing logic. Leaking internals exposes attack surface. A single documented error schema is a small upfront contract that pays off across every consumer forever.
- **Sources —** Google Cloud API Design Guide — "Errors" (cloud.google.com/apis/design/errors); RFC 7807 "Problem Details for HTTP APIs"; ENGINEERING_STANDARDS.md § API & interface design.

---

## Authoring rules (the catalog meta-rules — do not delete)

- **Additive floor:** add dimensions as you discover them; **never delete** one. This catalog is meant to become "every standard we can think of."
- **Right-size:** apply only *relevant* dimensions per change (`KISS`/`YAGNI`); never skip a relevant one. Relevance is a per-change judgment — see `README.md`.
- **Novel patterns allowed** when they add clear value — justify (problem → why existing patterns fall short → benefit) and record in `claugentic-DECISIONS.md`. Unconventional ≠ wrong.
- **Every dimension carries a Confidence tag** so the harness can separate what it *proved* (deterministic gates) from what it *asserts* (judgment). Trust the oracle, not the model's word.
