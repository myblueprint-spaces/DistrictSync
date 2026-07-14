<!-- claugentic-dev-harness@0.1.40 managed — do not edit (copied from the claugentic-dev-harness plugin) -->
---
module: testing
title: Testing
version: 0.1.0
status: draft
iso_25010: [maintainability, reliability, functional-suitability]
load_scope:
  keywords: [test, spec, coverage, mock, stub, fixture, characterization, regression, snapshot, e2e, mutation]
  globs: ["**/*.test.*", "**/*.spec.*", "**/test/**", "**/__tests__/**"]
last_reviewed: 2026-06-04
---

# Testing — does the suite actually prove the behavior, and will it catch the next regression?

> **Loads when:** any change that adds, edits, or deletes tests; any behavior change that *should* be tested; refactors of legacy/untested code (characterization first); UI work (visual + a11y); flaky-test triage; or coverage/CI-gate changes.
> **ISO/IEC 25010:** Maintainability (testability, analysability, modifiability), with Reliability and Functional-suitability — cross-cutting; tests are the safety net every other dimension leans on. · **Status:** draft · **v0.1.0**

Each entry below is one **auditable dimension**. Per change, the reviewer applies the
*relevant* ones **fully** (select-don't-skip), right-sized to the change — never
gold-plating an irrelevant one, never skipping a relevant one.

The governing rule across every dimension: **trust the oracle, not the author's word.** A test is only worth its green check if a real defect would have turned it red. "Tests pass" is necessary, never sufficient — the audit asks *what would have to break for this test to fail?*

---

## Test pyramid (shape of the suite: unit / integration / e2e)

- **Good looks like —** Most tests are fast, isolated **unit** tests (the wide base); fewer **integration** tests prove components wired together (real DB/queue/HTTP via testcontainers or in-memory doubles at trust boundaries); a thin top of **end-to-end** tests covers only critical user journeys. The new code's tests sit at the **lowest level that can still prove the behavior** — logic is unit-tested, not exercised only through a slow browser test. No "ice-cream cone" (e2e-heavy) or untested middle.
- **Auditor checks —** `[J]` Is each new behavior tested at the cheapest level that proves it, or pushed up into a slow/broad test out of convenience? `[J]` Is there an inverted pyramid (many e2e, few unit) signalling untestable design? `[D]` Test-suite wall-clock time and per-tier counts (where the runner reports tiers/tags). `[J]` Do integration tests exercise the *real* seam (DB, serialization, HTTP contract) rather than mocking the very thing under test?
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Lots of small fast tests find bugs in seconds and rarely break for the wrong reason; a few big slow tests are realistic but flaky and expensive. Get the mix wrong (all big tests) and the suite becomes so slow and unreliable the team stops trusting it.
- **Sources —** Test Pyramid — Mike Cohn, *Succeeding with Agile* (2009); Martin Fowler, "The Practical Test Pyramid" & bliki "TestPyramid"; *Software Engineering at Google* ch. 11 (test sizes: small/medium/large).

## Characterization tests & golden master (the equivalence oracle for legacy refactors)

- **Good looks like —** Before refactoring code that lacks tests, a **characterization test** pins the code's *current* observable behavior — not what it *should* do, what it *does* — so the refactor can be proven behavior-preserving. For wide/opaque output (a report, a render, a serialized blob), a **golden-master / approval** snapshot captures a representative input→output corpus as the reviewable baseline; the refactor passes iff outputs are byte-identical (or diffs are explicitly approved). Characterization tests are written *first*, then the change.
- **Auditor checks —** `[J]` Does a non-trivial refactor of previously-untested code introduce characterization/approval tests *before* the change (visible in commit order or PR narrative)? `[J]` Do they capture *actual* behavior (including known-quirky/buggy output) rather than asserting idealized behavior? `[D]` Golden-master baseline files are committed, diffable, and regenerable by a documented command. `[J]` Once the code is understood, are throwaway characterization tests promoted to intent-revealing tests or kept deliberately?
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** They let you safely change scary code you don't fully understand by locking in "whatever it does today" as the contract. The cost: they bless current bugs as expected (must be revisited later), and a too-thin corpus gives false safety. Skipping them on a legacy refactor means flying blind — silent behavior changes ship.
- **Sources —** Michael Feathers, *Working Effectively with Legacy Code* (2004) — coined "characterization test"; Golden Master / Approval Testing (ApprovalTests, `llewellyn falco`); understandlegacycode.com (characterization vs approval vs regression).

## Mutation testing (are the tests real?)

- **Good looks like —** For critical or complex logic, test strength is validated by **mutation testing**: a tool (Stryker for JS/TS, PIT for Java, `mutmut`/`cosmic-ray` for Python, etc.) injects small faults ("mutants" — flip `>` to `>=`, `&&` to `||`, delete a statement) and the suite must **kill** them (a test turns red). **Surviving mutants** flag assertions that are missing, too weak, or code paths exercised-but-not-verified. A mutation-score threshold guards critical modules; equivalent mutants are reviewed/suppressed, not ignored wholesale.
- **Auditor checks —** `[D]` Mutation run on the changed critical module; score meets the agreed threshold (e.g. ≥80% on core logic). `[J]` Are surviving mutants triaged (genuine gap → add assertion; equivalent → justified suppression) rather than blanket-ignored? `[J]` Is the tool aimed at logic that matters, not vanity-run across trivial code? `[D]` Mutation config/CI step present where the team has adopted it.
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** It's the only test that tests your tests — it proves a real bug would be caught, which coverage % cannot. The cost is runtime (it re-runs the suite once per mutant, so it's slow) and noise from "equivalent" mutants that can't be killed. Skipping it means high coverage can still hide assertion-free tests that never actually check anything.
- **Sources —** Mutation-testing literature — `R. DeMillo` et al. (1978) "Hints on Test Data Selection"; `Y. Jia` & `M. Harman`, "An Analysis and Survey of the Development of Mutation Testing" (IEEE TSE 2011); Stryker Mutator docs; PIT (pitest.org); *Software Engineering at Google* (coverage vs effectiveness).

## Coverage of behavior, not vanity %

- **Good looks like —** Tests assert **observable behavior and contracts** (outputs, state transitions, emitted events, error responses), not implementation internals — so they survive refactors and fail only on real regressions. New/changed behavior is covered; coverage % is a *floor signal*, never the goal. No assertion-free "coverage theater" (code executed to light up the line counter with no meaningful `expect`). Tests read as executable spec: arrange/act/assert, one reason to fail each.
- **Auditor checks —** `[J]` Do tests assert outcomes a user/caller cares about, or do they over-specify private internals and mock call-order? `[J]` Any tests that execute code but assert nothing (or only `not throws`) where a real outcome should be checked? (see Test-diff review dimension for the diff-coverage gate) `[J]` Would these tests fail if the behavior silently changed — or only if the implementation is rewritten?
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Testing what the code *does for the user* catches real bugs and lets you refactor freely; chasing a coverage number rewards tests that run code without checking it and that shatter on every refactor. 100% coverage with weak assertions can be worth less than 70% with sharp ones.
- **Sources —** Martin Fowler, "TestCoverage" (coverage as a tool, not a target) & "Goodhart's Law" framing; *Software Engineering at Google* ch. 11 (behavior over implementation; "test via public APIs"); Google Testing Blog, "Code Coverage Best Practices."

## Test-diff review (did the assertions/coverage get weaker?)

- **Good looks like —** The **test diff is reviewed as carefully as the production diff.** Changes that *weaken* the safety net are caught and justified: assertions loosened or deleted, expected values swapped to match new (possibly wrong) output, tests `skip`/`xit`/`@Disabled`/`it.only`-focused, `try/catch` swallowing a failing assertion, timeouts bumped to mask flakiness, or a golden-master baseline regenerated without explaining the diff. Net coverage and assertion strength on touched areas hold or improve.
- **Auditor checks —** `[D]` Grep the diff for `skip`, `only`, `xit`, `xdescribe`, `@Disabled`, `@Ignore`, `pytest.mark.skip`, `todo`, commented-out assertions, widened tolerances. `[J]` For each removed/loosened assertion or regenerated snapshot: is there a stated, legitimate reason? `[D]` Diff-coverage tool shows changed lines didn't drop below threshold. `[J]` Did an expected value change *to follow* the implementation (rubber-stamping a regression) rather than reflecting an intended behavior change?
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Reviewing test changes stops the most dangerous move in software — quietly deleting the alarm so the build goes green. It costs reviewer attention on files people skim. Skip it and a real regression ships with a passing suite because someone "fixed" the failing test instead of the bug.
- **Sources —** *Software Engineering at Google* ch. 9 (code review covers tests) & ch. 11; Google Testing Blog, "Testing on the Toilet: Change-Detector Tests Considered Harmful" (Jan 2015, https://testing.googleblog.com/2015/01/testing-on-toilet-change-detector-tests.html) — tests that pass through behavior changes / get rewritten to follow the code are worthless; Fowler, "SelfTestingCode."

## Failure-path & edge-case coverage

- **Good looks like —** Tests cover **the unhappy paths, not just the golden path**: invalid input, boundary values (0, 1, empty, max, off-by-one, negative), nulls/undefined, malformed/oversized payloads, timeouts, dependency failures, partial writes, concurrency/duplicate requests. Error handling is asserted by *behavior* — the right exception type/error shape, the right status, no partial state, the right log/no swallowed exception — matching the Correctness & Reliability standards. Each guard clause and `catch` has a test that drives it.
- **Auditor checks —** `[J]` Are boundaries and invalid inputs tested, or only the typical case? `[J]` Is every error branch / `catch` / fallback exercised and its *effect* asserted (not just "didn't crash")? `[D]` Branch coverage on the changed unit (catches untested error arms). `[J]` Are failure modes of external dependencies simulated (inject timeout/5xx/exception) and the degradation asserted?
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Most production incidents live on the unhappy path — bad input, a dependency down, a weird boundary — so testing those is where tests earn their keep. It costs more test cases for code that "rarely runs." Skip it and the code works perfectly in the demo and falls over on the first malformed request.
- **Sources —** Boris Beizer, *Software Testing Techniques* (boundary-value & equivalence-class analysis); *Software Engineering at Google* ch. 11; OWASP Web Security Testing Guide (WSTG) v4.2 §4.7 Input Validation Testing (https://owasp.org/www-project-web-security-testing-guide/v42/4-Web_Application_Security_Testing/07-Input_Validation_Testing/) for malformed/hostile-input cases; cross-ref `reliability-resilience` standards.

## Determinism (no flaky tests)

- **Good looks like —** Tests are **deterministic** — same code + same environment → same result, every run, in any order. Sources of nondeterminism are controlled: clock/time is injected or frozen (no `sleep`-then-assert, no real `now()`), randomness is seeded, test order is independent (no shared mutable state/leaked singletons), async is awaited on a condition (poll/await) not a fixed delay, and external I/O is stubbed or pinned. Flaky tests are **quarantined and fixed or deleted**, never left to "rerun until green" or normalize red builds.
- **Auditor checks —** `[D]` Grep for nondeterminism smells: `sleep(`, `setTimeout`-then-assert, unseeded `random`, `Date.now()`/`new Date()`/`time.time()` in assertions, order-dependent fixtures, `@Retry`/`flaky`/`rerun` annotations masking instability. `[J]` Is async synchronized via await/poll on a condition rather than a magic delay? `[D]` Suite passes under randomized order (`--shuffle`/`-p randomly`) and on repeat runs. `[J]` Are known-flaky tests tracked and being fixed, not silently retried?
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** A test that fails randomly is worse than no test — once people see false alarms they stop believing real ones, and the whole suite loses its meaning. Making tests deterministic costs upfront effort (inject the clock, seed the RNG). At Google, as flakiness approaches ~1% the suite starts losing value, so this isn't optional hygiene.
- **Sources —** Google Testing Blog, "Flaky Tests at Google and How We Mitigate Them" (16% of suite flaked at some point) & "Where do our flaky tests come from?"; Martin Fowler, "Eradicating Non-Determinism in Tests"; *Software Engineering at Google* ch. 11.

## Regression & snapshot tests

- **Good looks like —** Every fixed bug ships with a **regression test that fails on the old code and passes on the fix** — the bug cannot silently return. **Snapshot** tests (serialized output, rendered tree, API response shape) are used where output is wide and stable, with snapshots **committed, reviewed, and kept tight** (not giant blobs nobody reads). Snapshot updates are deliberate (`--update` run intentionally, diff inspected) — never reflexively accepted to make CI green.
- **Auditor checks —** `[J]` Does each bug fix include a test that pins the corrected behavior and would have caught the original bug? `[D]` Snapshot files are committed and present in the diff. `[J]` Are snapshots scoped/meaningful (focused on what matters) rather than sprawling captures that get rubber-stamped on update? `[J]` Was a snapshot update in this diff *justified by an intended change*, not a silent regression auto-accepted?
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** A regression test makes a bug fixable *once* — it can't come back unnoticed. Snapshots cheaply lock down large outputs but rot into noise if they're huge and blindly re-approved. The danger is treating "update the snapshot" as a reflex, which quietly accepts whatever broke.
- **Sources —** ISTQB Certified Tester Foundation Level (CTFL) Syllabus v4.0, §2.3 "Confirmation Testing and Regression Testing" (regression test = re-run after a change to catch newly introduced/uncovered defects), corroborated by the ISTQB Glossary "regression testing" entry (glossary.istqb.org); Kent C. Dodds, "Common Mistakes with React Testing Library" (https://kentcdodds.com/blog/common-mistakes-with-react-testing-library) — snapshot anti-patterns; Jest snapshot docs (https://jestjs.io/docs/snapshot-testing) & Vitest snapshot docs (https://vitest.dev/guide/snapshot) ("don't blindly update"); cross-ref Characterization/Golden-master above (a snapshot is a golden master).

## Visual-regression & accessibility testing (UI)

- **Good looks like —** UI changes are guarded by **visual-regression** tests (Playwright/Chromatic/Percy/Storybook) that diff rendered screenshots against an approved baseline across key viewports/themes, so unintended layout/style shifts are caught and intended ones are explicitly approved. **Automated accessibility** checks (axe-core via Playwright/Cypress/Storybook, or `jest-axe`) run on components/pages and fail the build on WCAG violations — understood as a **floor** (Deque's 2021 study found axe-core detects ~57% of issues; traditional tool estimates range from ~30%; see Sources), backed by keyboard-nav and screen-reader/manual checks per the `product-ux` standard.
- **Auditor checks —** `[D]` axe/jest-axe assertions present on changed UI; build fails on new violations. `[D]` Visual baselines committed and diffs reviewed/approved (not auto-accepted). `[J]` Are critical states (loading/empty/error, focus, RTL, dark mode) captured, not just the happy render? `[J]` Is automated a11y treated as a floor with manual keyboard/SR coverage for what scanners miss?
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Screenshot diffs catch "it looks broken" that unit tests can't see, and axe catches the easy half of accessibility bugs automatically. The cost is baseline maintenance (renders shift, baselines need re-approval) and false diffs from anti-aliasing/timing. Skip it and visual regressions and excluded users ship unnoticed.
- **Sources —** Deque, "Automated Testing Study Identifies 57% of Digital Accessibility Issues" (2021) — https://www.deque.com/blog/automated-testing-study-identifies-57-percent-of-digital-accessibility-issues/ (axe-core, 13,000+ pages, ~300,000 issues); axe-core GitHub (https://github.com/dequelabs/axe-core) — WCAG 2.0/2.1/2.2 A/AA/AAA rule set; Chromatic accessibility-test addon docs (https://www.chromatic.com/docs/accessibility); Playwright axe integration (https://playwright.dev/docs/accessibility-testing); WCAG 2.2 (https://www.w3.org/TR/WCAG22/); cross-ref `product-ux`.

## Contract testing (provider/consumer compatibility)

- **Good looks like —** Across a service/API boundary, **contract tests** verify provider and consumer agree on the wire format *without* a full integrated environment. **Consumer-driven contracts** (Pact + a broker) let the consumer declare expected interactions; the provider verifies the recorded pact against its real implementation, and the broker gates deploy with "can-I-deploy" so a breaking change is caught at build time, not in prod. Schema/contract checks (OpenAPI/JSON-Schema/protobuf back-compat, e.g. Buf breaking-change detection) guard published interfaces, aligned with the `api-and-contracts` standard.
- **Auditor checks —** `[J]` Do changes to a published API/event have a contract test (pact, schema-compat, or stored-response verification) rather than relying on slow brittle end-to-end? `[D]` Provider verification / schema-back-compat check runs in CI and passes; broker "can-I-deploy" green where adopted. `[J]` Is the contract the genuine consumer expectation, kept in sync, not a stale hand-written stub? `[D]` Breaking-change detector (Buf/OpenAPI-diff) gates the published surface.
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Contract tests catch "you broke my caller" at build time and let independent services deploy without a giant shared test environment. The cost is the broker/tooling and discipline to keep contracts current. Skip it and integration breakages surface only in production, or you pay for slow flaky end-to-end suites to find them.
- **Sources —** Consumer-Driven Contracts — Ian Robinson / Martin Fowler ("ConsumerDrivenContracts"); Pact (docs.pact.io, Pact Broker / can-i-deploy); Buf breaking-change detection; cross-ref `api-and-contracts`.

---

## Authoring rules (the catalog meta-rules — do not delete)

- **Additive floor:** add dimensions as you discover them; **never delete** one. This catalog is meant to become "every standard we can think of."
- **Right-size:** apply only *relevant* dimensions per change (`KISS`/`YAGNI`); never skip a relevant one. Relevance is a per-change judgment — see `README.md`.
- **Novel patterns allowed** when they add clear value — justify (problem → why existing patterns fall short → benefit) and record in `claugentic-DECISIONS.md`. Unconventional ≠ wrong.
- **Every dimension carries a Confidence tag** so the harness can separate what it *proved* (deterministic gates) from what it *asserts* (judgment). Trust the oracle, not the model's word.
