<!-- claugentic-dev-harness@0.1.40 managed — do not edit (copied from the claugentic-dev-harness plugin) -->
---
# ── Module contract (copied from _TEMPLATE.md) ──
module: maintainability-structure
title: Maintainability & Structure
version: 0.1.0
status: draft
iso_25010: [maintainability]
load_scope:
  keywords: [refactor, architecture, module, layer, service, pattern, coupling, cohesion, naming, dead-code, complexity, types]
  globs: ["src/**", "lib/**", "**/*.ts", "**/*.js"]
last_reviewed: 2026-06-04
---

# Maintainability & Structure — is this code shaped so the next change is cheap?

> **Loads when:** new/changed code introduces or reshapes structure — modules, layers, services, interfaces, abstractions; refactors; anything where SOLID, layering, design-pattern choice, coupling/cohesion, type-safety, naming, or code-health is in play.
> **ISO/IEC 25010:** Maintainability (modularity, reusability, analysability, modifiability, testability) · **Status:** draft · **v0.1.0**

Each entry below is one **auditable dimension**. Per change, the reviewer applies the
*relevant* ones **fully** (select-don't-skip), right-sized to the change — never
gold-plating an irrelevant one, never skipping a relevant one. Tags: `[D]` a gate/tool
can prove it · `[J]` needs a reviewer's eye.

---

## SOLID

- **Good looks like —** Each unit has **one reason to change** (SRP); behavior extends without editing stable code (OCP — see *Open/Closed & config-driven* dimension); subtypes honor their base's contract — no surprises, no strengthened preconditions / weakened postconditions (LSP); clients depend on **narrow role interfaces**, not fat ones (ISP); high-level policy depends on **abstractions**, not concretions (DIP — see *Dependency direction* dimension).
- **Auditor checks —** `[J]` Does any class/function mix unrelated responsibilities (e.g. HTTP parsing + business rules + SQL in one place)? (SRP) `[J]` Does adding a case require editing a `switch`/`if-else` over a closed set instead of adding a type (OCP smell — full treatment in *Open/Closed & config-driven* dimension)? `[J]` Does a subtype throw `NotImplemented`, no-op an inherited method, or narrow accepted inputs (LSP break)? `[D]` Interface width — flag interfaces whose implementers stub out methods they don't use (ISP). *(DIP import-direction is audited in the *Dependency direction* dimension — see there.)*
- **Confidence —** `mixed` — SRP/OCP/LSP are `[J]` reviewer calls; ISP is partly `[D]` where an interface-width / unused-member linter flags stubbed methods. *(DIP, the other deterministic-capable check, lives in the *Dependency direction* dimension.)*
- **Tradeoff (plain English) —** Code that follows these five rules costs a little more up front to shape, but each later change touches one small place instead of rippling everywhere. Skip them and the system calcifies — every "small" change becomes risky and slow.
- **Sources —** R.C. Martin, *Agile Software Development / Clean Architecture* (SOLID); B. Liskov, "Data Abstraction and Hierarchy" (LSP); B. Meyer, design-by-contract (pre/post-conditions).

## Separation of concerns

- **Good looks like —** Distinct concerns live in distinct units: HTTP/transport, orchestration, business rules, persistence, and presentation don't bleed into each other. A change to one concern (swap the DB, change the wire format) doesn't force edits across the others.
- **Auditor checks —** `[J]` Is there business logic inside a controller/handler, an ORM call inside a domain entity, or formatting inside a repository? `[J]` Can you name the single concern each module owns in one phrase? `[D]` Layer-boundary lint (import-linter / dependency-cruiser / `madge`) flags cross-concern imports where rules exist.
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Keeping concerns apart means a UI change can't break a billing rule and a database swap can't break the UI. The cost is more files and a little indirection; the payoff is changes that stay contained.
- **Sources —** Dijkstra, "On the role of scientific thought" (origin of *separation of concerns*); R.C. Martin, *Clean Architecture*.

## Architectural layers (Clean / Hexagonal / Onion)

- **Good looks like —** Code organizes into rings with **dependencies pointing inward only** (The Dependency Rule): **Domain/Entities** (pure business types & invariants, zero framework imports) ← **Application/Use-cases** (orchestration, transaction script / service layer) ← **Ports** (interfaces the inner rings own) ← **Adapters** (presentation/**API**/controllers, **repository** impls, gateways, message consumers — the replaceable edge). At the outer edge sits a discrete **validation layer — schema/contract enforcement at the edge, never inside domain objects**: untrusted input is validated/parsed into a typed shape before it crosses inward. **DTOs** carry data across the boundary; mapping happens at the edge; an **anti-corruption layer** translates a foreign model so it can't leak inward. The domain never imports the web framework, the ORM, or an external SDK.
- **Auditor checks —** `[D]` Does any inner-ring file import an outer-ring concern (framework, ORM, HTTP, SDK)? — provable with an import-boundary linter (import-linter contracts / dependency-cruiser / ArchUnit-style). `[J]` Are ports defined by the **inner** ring and implemented by the **outer** ring (dependency inversion at the boundary), not the reverse? `[J]` Do DTOs exist at the boundary so the domain model isn't serialized straight to the wire? `[J]` Is validation logic confined to the boundary and absent from domain entities / use-case code? `[J]` Is there an ACL/translator where an external/legacy model enters?
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Layering lets you replace the database, the web framework, or a third-party vendor without rewriting your business rules — and lets you test those rules without spinning up any of them. The cost is more boundaries and mapping code; for a tiny script it's overkill (apply right-sized).
- **Sources —** R.C. Martin, *Clean Architecture* (The Dependency Rule, 2012); A. Cockburn, *Hexagonal / Ports & Adapters* (2005); J. Palermo, *Onion Architecture* (2008); E. Evans, *DDD* (anti-corruption layer, layered architecture); Fowler, *PoEAA* (Service Layer, DTO).

## Design-pattern catalog (use the right one — or justify a novel one)

- **Good looks like —** When a recurring problem appears, a **named, established pattern** is applied deliberately (not cargo-culted): **persistence/data** — Repository, Unit-of-Work, Specification, Data Mapper, Gateway; **behavioral** — Strategy, Observer, Mediator, Template Method, Command; **creational** — Factory / Abstract Factory, Builder, Dependency Injection; **structural** — Decorator, Adapter, Facade, Proxy, Composite; **resilience/distributed** — Circuit-Breaker, Bulkhead, Saga (orchestration/choreography), Transactional Outbox, Retry, Timeout. A pattern is used where it earns its keep, named in code/comments/commit, and not layered on where a plain function would do (over-engineering is itself a smell). A **novel** pattern is allowed only with a written justification (problem → why existing patterns fall short → benefit) recorded in `claugentic-DECISIONS.md`.
- **Auditor checks —** `[J]` Does the chosen pattern actually fit the problem, or is it pattern-for-pattern's-sake (e.g. a Factory that only ever makes one type, a Strategy with one strategy)? `[J]` Is data access behind a **Repository** (collection-like interface) rather than ORM/SQL scattered through services? `[J]` Where multiple writes must commit together, is **Unit-of-Work / transaction** boundary explicit? `[J]` For cross-service writes, is **Saga + Outbox** used instead of an unsafe dual-write? `[J]` For unreliable I/O, are **Circuit-Breaker / Bulkhead / Timeout / Retry** present where apt (cross-ref `reliability-resilience`)? `[J]` If a pattern is novel/unconventional, is the justification recorded?
- **Confidence —** `judgment`
- **Tradeoff (plain English) —** Reaching for a well-known pattern means the next engineer recognizes the shape instantly and the solution is battle-tested. The cost is indirection — and forcing a pattern where none is needed makes simple code harder to read, so match the pattern to the problem.
- **Sources —** GoF, *Design Patterns* (Strategy, Observer, Factory, Decorator, Adapter, Facade, Mediator, Composite, Proxy, Template Method, Command); Fowler, *PoEAA* (Repository, Unit of Work, Data Mapper, Gateway, Service Layer); E. Evans, *DDD* (Repository, Specification, Factory); M. Nygard, *Release It!* (Circuit-Breaker, Bulkhead, Timeout); C. Richardson, *microservices.io* (Saga, Transactional Outbox).

## Composition over inheritance

- **Good looks like —** Reuse and variation come from **composing** small collaborators (has-a, delegation, injected strategies) rather than deep `extends` chains. Inheritance is reserved for genuine **is-a substitutability** (passes LSP); no inheritance used purely for code reuse.
- **Auditor checks —** `[J]` Is there an inheritance hierarchy deeper than ~2 levels, or one used only to share helper code? `[J]` Could a `extends` relationship be a constructor-injected collaborator instead? `[J]` Any "parallel inheritance hierarchies" smell (adding a subclass here forces a subclass there)?
- **Confidence —** `judgment`
- **Tradeoff (plain English) —** Building behavior by plugging pieces together keeps each piece swappable and testable; deep inheritance trees are rigid and a change near the root can break every descendant unexpectedly. Composition costs a little more wiring for far more flexibility.
- **Sources —** GoF, *Design Patterns* ("favor object composition over class inheritance"); Fowler, *Refactoring* (Replace Inheritance with Delegation).

## Dependency direction (DIP) & make invalid states unrepresentable

- **Good looks like —** Dependencies flow toward **stable abstractions**; volatile details (DB, network, vendor SDK) sit behind interfaces the stable core owns. The **type system makes illegal states impossible** — sum/union types and enums over stringly-typed flags, non-nullable by default, value objects/branded types that validate on construction so an invalid instance can't exist, exhaustive matches the compiler enforces.
- **Auditor checks —** `[D]` Import direction — where an import-boundary linter is configured (import-linter / dependency-cruiser / ESLint `no-restricted-imports`), does it flag violations of "imports point toward abstractions"? `[J]` Without tooling, manually trace the import graph for DIP violations — do high-level modules reach for concrete low-level details instead of an abstraction? `[J]` Are impossible combinations representable (e.g. `status: string` + nullable fields that "shouldn't" coexist) where a discriminated union would forbid them? `[D]` Exhaustiveness — `switch` over a union has a `never`/exhaustive default that fails the type-checker on a missing case. `[J]` Do constructors/parsers validate at the boundary so downstream code can trust the type (parse, don't validate)?
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** When the types themselves rule out bad data, whole classes of bugs simply can't be written, and the compiler catches mistakes before they ship. The cost is more deliberate up-front modeling; the payoff is fewer runtime surprises and fearless refactors.
- **Sources —** R.C. Martin, DIP & SDP/SAP (*Clean Architecture*); Y. Minsky, "Effective ML" (Jane Street / Harvard CS51, 2010 — coined *make illegal states unrepresentable*); A. King, "Parse, don't validate" (2019 — the distinct parse-over-validate discipline); S. Wlaschin, *Domain Modeling Made Functional* (type-driven design; both ideas applied in F#/DDD).

## Code health & housekeeping (smells, complexity, duplication, naming, comments)

- **Good looks like —** **No dead code** (unreachable branches, unused exports/params/vars, commented-out blocks, feature-flag corpses). The **Fowler smells** are absent or paid down: *Bloaters* (Long Method, Large Class, Long Parameter List, Data Clumps, Primitive Obsession), *OO-Abusers* (Switch Statements over types, Refused Bequest), *Change-Preventers* (Divergent Change, Shotgun Surgery, Parallel Inheritance), *Dispensables* (Duplicate Code, Dead Code, Speculative Generality, Comments-as-deodorant), *Couplers* (Feature Envy, Inappropriate Intimacy, Message Chains, Middle Man). **Cyclomatic complexity** per function stays within the project threshold; **duplication** is below threshold (DRY). **Names** reveal intent (searchable, pronounceable, domain-aligned, no misleading or `tmp2` names). **Comments explain *why*, not *what*** — code is the source of truth; no stale or redundant comments.
- **Auditor checks —** `[D]` Dead/unused code — linter (`ts-prune`/`knip`, ESLint `no-unused-vars`, `vulture`, compiler unused flags) reports zero new. `[D]` Cyclomatic/cognitive complexity over threshold — ESLint `complexity` / SonarQube / `radon` / `lizard`. `[D]` Duplication over threshold — `jscpd` / SonarQube copy-paste detector. `[D]` Commented-out code blocks present? (lint/grep). `[J]` Do names read as intent at the call site; any abbreviations or misleading names? `[J]` Are comments load-bearing *why*-comments, or do they restate the code / lie about it?
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Code kept clean — nothing unused, nothing duplicated, nothing needlessly tangled, names that say what they mean — is fast and safe to change. Letting smells accumulate is borrowing against the future: each one makes the next change a little slower and a little riskier until the area becomes "scary to touch."
- **Sources —** M. Fowler, *Refactoring* (the smell catalog & its five categories); McCabe, "A Complexity Measure" (cyclomatic complexity); R.C. Martin, *Clean Code* (naming, functions, comments); B. Kernighan & P. Plauger, *Elements of Programming Style*.

## Type safety

- **Good looks like —** Static typing is **on and strict** (e.g. TS `strict`, `noImplicitAny`, `noUncheckedIndexedAccess`; mypy `--strict`); `any`/`unknown`-without-narrowing, unchecked casts, and `@ts-ignore`/`# type: ignore` are absent or each carry a justified, narrowly-scoped comment. Public function signatures and module boundaries are fully typed; external/`unknown` input is validated into a typed shape at the boundary (schema → type), not cast.
- **Auditor checks —** `[D]` Type-checker passes with strict config (`tsc --noEmit`, `mypy --strict`) — no new errors and no new suppressions. `[D]` Count of `any`/`@ts-ignore`/`type: ignore` introduced (lint rule `no-explicit-any`, `no-ts-ignore`). `[J]` Is untrusted input *parsed* into a type at the edge (e.g. zod/pydantic) rather than asserted with `as`?
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Strong, strict types let the compiler catch a huge share of mistakes for free and make the code self-documenting; escaping the type system with `any`/casts silently re-opens the door to those bugs. Strictness costs some friction up front and pays it back at every change.
- **Sources —** TypeScript Handbook, "TypeScript Strict Mode" (https://www.typescriptlang.org/tsconfig#strict); mypy, "–strict flag" (https://mypy.readthedocs.io/en/stable/command_line.html#cmdoption-mypy-strict); S. Wlaschin, *Domain Modeling Made Functional* (Pragmatic Programmers, 2018) — type-driven design; A. King, "Parse, don't validate" (2019, https://lexi-lambda.github.io/blog/2019/11/05/parse-don-t-validate/).

## Open/Closed & config-driven

- **Good looks like —** Behavior that varies (by tenant, environment, locale, feature) is driven by **configuration/registration/strategy** so new variants are *added* without editing stable code. New cases register via a map/plugin/strategy table rather than growing a hard-coded `switch`. Magic numbers/strings are named constants from a single source.
- **Auditor checks —** `[J]` Does adding a variant require editing a central conditional, or registering a new entry? `[J]` Are environment/behavioral knobs configurable (not hardcoded per-env)? `[D]` Magic-literal lint where configured (`no-magic-numbers`).
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** When variation is data-driven, adding the next case is a safe, small change instead of surgery on tested code. The cost is an indirection layer that isn't worth it until variation actually exists (YAGNI — don't pre-build it).
- **Sources —** B. Meyer, *Object-Oriented Software Construction* 2nd ed. (Prentice Hall, 1997) — Open/Closed Principle; R.C. Martin, *Agile Software Development, Principles, Patterns, and Practices* (Prentice Hall, 2002) ch. 9 — OCP; Wiggins et al., *The Twelve-Factor App* Factor III — Config (https://12factor.net/config).

## Clear contracts & interfaces

- **Good looks like —** Each module exposes a **small, intention-revealing public surface**; internals are not exported. Inputs/outputs/errors and invariants are explicit (types + the occasional doc/contract). Callers depend on the **interface**, not on internal structure. Breaking changes to a published contract are versioned (cross-ref `api-and-contracts`).
- **Auditor checks —** `[J]` Is the public API minimal, or does it leak internals (helpers, mutable state, concrete types where an interface belongs)? `[J]` Are error/edge outcomes part of the contract (typed errors / documented), not surprises? `[D]` Public-surface lint (`no internal export` / package `exports` map / API-extractor) where configured.
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** A narrow, well-named interface lets you change the inside freely without breaking callers and tells the next engineer exactly how to use it. A leaky, sprawling surface ties your hands — every internal becomes something a caller might depend on.
- **Sources —** D. Parnas, "On the Criteria To Be Used in Decomposing Systems into Modules," *CACM* 15(12) (1972) — information hiding; R.C. Martin, *Agile Software Development, Principles, Patterns, and Practices* (Prentice Hall, 2002) ch. 12 — Interface Segregation Principle; J. Bloch, *Effective Java* 3rd ed. (Addison-Wesley, 2018) Items 15 & 64 — "minimize accessibility", "design APIs deliberately".

## Low coupling / high cohesion

- **Good looks like —** Modules are **cohesive** (everything inside relates to one purpose) and **loosely coupled** (few, narrow, stable connections; no reaching into another module's internals; dependencies via interfaces/injection). No cyclic dependencies between modules. Changes stay local.
- **Auditor checks —** `[D]` **Cyclic dependencies** — `madge --circular` / dependency-cruiser / import-linter reports none. `[D]` Fan-in/fan-out & instability metrics where tooled (dependency-cruiser, SonarQube). `[J]` Does a module reach across a boundary into another's internals (high coupling) or own unrelated responsibilities (low cohesion)? `[J]` Is wiring done via injected interfaces rather than `new`-ing concretes inside business code?
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Loosely-coupled, cohesive modules can be understood, tested, and changed one at a time; tightly-coupled ones move as a tangled clump where touching one thing breaks three others. The cost is the discipline of boundaries and a little wiring.
- **Sources —** Stevens, Myers & Constantine, "Structured Design" (origin of coupling & cohesion); R.C. Martin (ADP — Acyclic Dependencies Principle; coupling/instability metrics, *Clean Architecture*); Parnas (information hiding).

---

## Authoring rules (the catalog meta-rules — do not delete)

- **Additive floor:** add dimensions as you discover them; **never delete** one. This catalog is meant to become "every standard we can think of."
- **Right-size:** apply only *relevant* dimensions per change (`KISS`/`YAGNI`); never skip a relevant one. Relevance is a per-change judgment — see `README.md`.
- **Novel patterns allowed** when they add clear value — justify (problem → why existing patterns fall short → benefit) and record in `claugentic-DECISIONS.md`. Unconventional ≠ wrong.
- **Every dimension carries a Confidence tag** so the harness can separate what it *proved* (deterministic gates) from what it *asserts* (judgment). Trust the oracle, not the model's word.
