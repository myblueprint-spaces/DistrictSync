<!-- claugentic-dev-harness@0.2.4 managed — do not edit (copied from the claugentic-dev-harness plugin) -->
# Agent Development Workflow

> ### 👋 New here? Read this first
> This repo is built and maintained with an **agent-assisted workflow**:
> - **Small/local change?** Just make it — tests must pass, and the architecture-tree hook will prompt you to document any new source file in `docs/claugentic-ARCHITECTURE_TREE.md`.
> - **Substantial change?** (new subsystem, cross-cutting refactor, shared-contract change, or **~8+ files**) — don't free-code. The agent will **pause, ask questions, enter plan mode**, and run the pipeline below: a plan in `.claude/plans/`, an adversarial review, a spec **you approve**, implementation in an isolated branch, verification, then a retrospect that improves this harness.
> - **Your map of the codebase** is `docs/claugentic-ARCHITECTURE_TREE.md` (one line per file) — read it before diving into source. Specialist **roles** the agent delegates to live in `.claude/agents/` and grow over time.
> - **Decisions** → `docs/claugentic-DECISIONS.md`. **Backlog** → `docs/claugentic-ROADMAP.md`.
> - **Build mode** (`skills/build/SKILL.md`) is the orchestration layer that **auto-drives this pipeline over the audit backlog** — pick items or a whole tier and it works them through the stages below, pausing only at the decisions that are yours.

How agents **and** human devs take *substantial* work from idea → landed change while keeping quality high and the harness self-improving. CLAUDE.md links here; this is the source of truth for process.

> **One-line:** Triage → Discuss → Plan → Review the plan → Spec → **Approve** → Implement → Verify → Land → **Retrospect**. Small changes skip to Implement+Verify.

---

## 0. Triage — does this need the full pipeline?

**Full pipeline** for *substantial* work: a new subsystem, a cross-cutting refactor, a change to a shared contract/pattern/standard, a security boundary, or anything touching **roughly 8+ files** (or a pattern documented in CLAUDE.md / STANDARDS).

**Lightweight path** for small/local/mechanical changes: go straight to **Implement → Verify**, still updating `claugentic-ARCHITECTURE_TREE.md` and `claugentic-DECISIONS.md` as needed.

**Triage continuously, not just up front.** A conversation often *grows* into a substantial change. The moment a request is shaping up to cross the bar above (≈8 files, or any qualitative trigger), **stop free-coding**: ask the user clarifying questions — from technical *and* **product-discovery** angles (see Stage 1) — until scope is crystal-clear, then **enter plan mode (Stage 2)** and follow the pipeline — don't keep ad-hoc editing.

When unsure, default to full; the plan-reviewer (Stage 3) confirms the path was right.

---

## Principles (apply at every stage)

- **Slice small, land complete.** Every unit of work must be finishable by **one specialist agent in a single ≤1M-token-context session** and land **vertically complete** — no half-done state, no `TODO`/debt left behind. If it doesn't fit, decompose further *before* implementing. This is a hard gate, not a guideline.
- **No new tech debt.** A landed slice leaves the codebase at least as clean as it found it: tests added, docs/ARCHITECTURE_TREE updated, no dead code, no silenced errors.
- **The harness is living.** Any task may improve STANDARDS / CLAUDE.md / the `.claude/agents/` role library / this workflow. Stage 9 is how that happens; treat harness improvements as first-class output, not a chore.
- **Delegate liberally to preserve orchestrator context.** Use subagents freely and in parallel — **no resource constraints** — so the orchestrator's own context stays lean for synthesis and decisions (fan out reads, reviews, and implementation to specialists). The orchestrator picks whichever role(s) fit from the `.claude/agents/` library; as the library grows it has more specialists to choose from.
- **Effort-dial the machine.** Scale review/verification depth to the change's risk and size, and load only the standards modules the change *touches* (relevance-gating). The dial flips on the **same triggers Stage 0 uses to call work *substantial*** — a **security/trust boundary · a shared contract/pattern/standard · ~8+ files · or any trust/honesty surface** (per the diverse-critics principle below): a small/local change gets a quick **solo `architect-reviewer`** look; any of those triggers fans out the lenses (and, on a trust surface, the diverse panel — that principle names *who* joins; this names *when* to fan out). Don't run the whole machine on a trivial change — that's how a harness kills the velocity it's meant to protect.
- **Diverse critics on contested or trust-surface changes.** A contested design fork OR a trust/honesty surface (claims, `[D]`/`[J]` labels, proof-vs-attempt wording, a security boundary) triggers the **diverse panel** — `plan-reviewer`/`architect-reviewer` + `yagni-sentinel` + `honesty-reviewer`, **plus `product-designer` when the change is user-facing** (so the review confirms the plan still achieves what the user is trying to achieve, not just that it's technically sound) — at **every gate that change passes** (Plan **and** Verify); else a lone reviewer suffices. At **Verify**, the wired mechanism is `engine/verify.js`: the orchestrator **invokes** it (the invocation is model-upheld — the platform does not auto-fire workflows), and the script then convenes the panel mechanically; if the Workflow tool is unavailable, say so, convene the panel in prose, and tag the run **"prose-orchestrated"** — never claim script guarantees on a prose run. At **Plan**, the panel remains prose-convened.
- **Review is cross-review by separate specialist agents — each on the most capable available model, each clean-context.** The gate/refute roles — `plan-reviewer` · `architect-reviewer` · `honesty-reviewer` · `finding-verifier` — are **distinct specialist agents** (separate roles, separate contexts); each works only from `{claim, file:line, …}` and **never sees the builder's rationale or transcript**, so it cannot rubber-stamp the reasoning. The independence is of **context and role** — a separate agent, a clean contract, a single lens — **not** of model: every agent runs the most capable available model, so blind spots can still correlate, and the harness says so (it does **not** claim model-independent review). **Honest claim:** multiple skeptical, clean-context specialist passes on the most capable model — a reduction of rubber-stamping risk, never a guarantee. *(The engine keeps a `RUNNING AS` / `sameModelTag` note as an honest per-run model-relationship reporter; it no longer engineers cross-model de-correlation.)*
- **Model tiers — by capability, never a pinned version.** The harness picks models by capability *tier* via aliases that auto-resolve to the current model in that tier: **most capable** (`opus`) · **mid / cheaper** (`sonnet`) · **fastest / cheapest** (`haiku`). Default to the **most capable available model** for anything needing judgment — *including high-volume review*; drop to a cheaper tier only for genuinely mechanical work. **Accuracy first, usage-conservation second.** The tier→alias mapping is the single thing to change if a tier is ever renamed (the engine `MODELS` block + the agents' `model:` frontmatter).
- Plus the CLAUDE.md non-negotiables: SOLID > DRY > KISS > YAGNI · fail loudly · validate at boundaries · configurable-over-hardcoded · single source of truth.

---

## Context & handoff

- **The durable memory is the plan file + `claugentic-DECISIONS.md` + `claugentic-ARCHITECTURE_TREE.md`** — keep them current as you go. For long work, a **fresh session resuming from the plan checklist beats a deeply-compacted context**; no manual orchestrator "handover" is needed.
- **Delegate token-heavy work to subagents** to keep the orchestrator lean (the *Delegate liberally* principle above — this is its context payoff).

---

## Roles — a library, not a fixed pair

The orchestrator **selects the role(s) that fit the task** and may spawn several or compose them. It is not locked to a fixed sequence of agents.

Starter library (`.claude/agents/`):
- **`plan-reviewer`** — adversarially critiques a plan (correctness, SOLID/patterns, risk, **sizing & completeness**, over-engineering/YAGNI, harness impact); writes findings into the plan file.
- **`implementer-architect`** — implements an approved spec to standard, in an isolated worktree, landing one slice complete.
- **`product-designer`** — the product/UX lens at Discuss (Stage 1) for user-facing work: user, job-to-be-done, flows, states, "what good feels like"; applies `docs/claugentic-standards/product-ux.md`, persists to `docs/claugentic-PRODUCT.md`.
- **`product-critic`** — critiques a draft product spec by method and returns proposals the user decides on; the elevate counterpart to `product-designer` (builder-class, read-only).
- **`architect-reviewer`** — owns the Verify gate (Stage 7): **solo** for small changes, or **synthesizer** over fan-out findings for risky ones.
- **`lens-reviewer`** — audits a **diff (Verify) or an audit-scope (the `audit` skill)** against **one** `docs/claugentic-standards/` module; invoked once per relevant lens in a fan-out review.
- **`finding-verifier`** — refutes **one** audit finding against the code: given only the claim + `file:line` (never the finder's rationale), it tries to prove the finding wrong and returns `Verified` / `Refuted` / `Unconfirmed`. The audit's adversarial-verify counterpart to `lens-reviewer`.
- **`blindspot-reviewer`** — the audit's cross-cutting / between-the-modules sweep (the `thorough` dial's diverse blind-spot finder): its lens is the *whole scope*, red-teaming for the risk **no single module-lens owns** (emergent architectural smells, integration gaps, systemic cross-cutting issues). Returns the same finding shape as `lens-reviewer`, so it joins the same dedup → prune → verify path; always `exhaustive` depth.
- **`yagni-sentinel`** — the anti-over-engineering skeptic; argues a plan/diff is *too much*. The deliberate counterweight to the quality lenses. (Also the audit's `thorough`-only adversarial prune over consolidated findings.)
- **`honesty-reviewer`** — the claims / over-claim lens; refutes **copy** (not code), flagging text that launders model-or-human-upheld judgment into apparent mechanical fact (the verb discipline · `[D]`/`[J]` label integrity · dimension-scoped success claims). Part of the diverse panel at Plan + Verify on trust/honesty surfaces.

Also available without new files: built-in **`Explore`** (fan-out search), **`Plan`** (drafting), and `/code-review` · `/simplify` for diff cleanup. **Add new role files as needs emerge** — the library grows (Stage 9).

---

## The pipeline

**The ten stages group into four beats — `FRAME → APPROVE → BUILD → CLOSE`** (a one-glance map; no stage is cut or renumbered):

- **FRAME** (0 Triage · 1 Discuss · 2 Plan · 3 Review · 4 Spec) — converge on *what to build and how*.
- **APPROVE** (5) — the user's sign-off; *no code before this*.
- **BUILD** (6 Implement · 7 Verify) — build it and check it.
- **CLOSE** (8 Land · 9 Retrospect) — ship it, harvest the lessons.

| # | Stage | Beat | Owner | Output |
|---|-------|------|-------|--------|
| 0 | **Triage** | FRAME | orchestrator | full vs lightweight path |
| 1 | **Discuss & brainstorm** | FRAME | orchestrator + **user** | learn the endeavour from multiple **angles** before planning — technical scope **+ product-discovery** (who's the user · the job-to-be-done · what success/"delight" looks like · the key flows & their states); these surface the user-story gaps a technical-only intake misses. User-facing work pulls in `product-designer` → `docs/claugentic-PRODUCT.md`. Crystal-clear scope; tangents→ROADMAP, decisions→DECISIONS |
| 2 | **Draft plan** | FRAME | orchestrator / `Plan` | `.claude/plans/NNNN-<slug>.md` (Problem · Approach · Risks · Test strategy · slices — Review & Spec filled later), **sliced into ≤1-session units** |
| 3 | **Review the plan** | FRAME | `plan-reviewer` (+ others as fit) | critique written into the plan's *Review* section; iterate until it passes the gate |
| 4 | **Spec** | FRAME | orchestrator | plan upgraded to implementation-ready spec **per slice**: opens with a short plain-English block (*what this builds · what "done" means for you · what you're accepting — risks/trade-offs*), then file-by-file changes, signatures, test list, acceptance criteria, **+ the in-scope `docs/claugentic-standards/` dimensions & target bar (entry point: `docs/claugentic-ENGINEERING_STANDARDS.md`)** |
| 5 | **Approval gate** | APPROVE | **user** | sign-off on the spec — *no code before this* |
| 6 | **Implement** | BUILD | `implementer-architect` | one slice/session, isolated worktree/branch; upholds CLAUDE.md; updates ARCHITECTURE_TREE inline |
| 7 | **Verify** | BUILD | `architect-reviewer` (+ lenses) | **effort-dialed** on the same triggers that flip Stage-0 to *substantial* — a security/trust boundary · a shared contract/pattern/standard · ~8+ files · any trust/honesty surface (per the diverse-panel principle): a **small/local** change → `architect-reviewer` audits solo; any of those triggers → fan out the panel via **`engine/verify.js`** (the orchestrator invokes it — `${CLAUDE_PLUGIN_ROOT}/engine/verify.js` for an adopter, the repo-local `./engine/verify.js` dogfooding this repo; spike-verified read-from-install-path) — one `lens-reviewer` per relevant `docs/claugentic-standards/` module **+** `yagni-sentinel` (**+ `honesty-reviewer`** on a trust surface), then `architect-reviewer` **synthesizes**, with the judge `model:` (the most-capable tier) and same-model tag computed in code. Workflow tool unavailable → convene in prose and tag the run **"prose-orchestrated."** **All Definition-of-Done gates green** (see below); run **`/simplify`** + **`/code-review`**; confirm spec match. Findings are **dual-layer** (technical + plain-English). |
| 8 | **Land** | CLOSE | orchestrator | conventional commit/PR; remove the completed plan from `.claude/plans/` (git history keeps it); append DECISIONS |
| 9 | **Retrospect & evolve** | CLOSE | orchestrator | harvest learnings into the harness (see below) |

**Stage 3 gate — a plan may not pass review until:** it is correct & sound (SOLID/patterns), each slice is **session-sized and lands complete with no debt**, the right path was chosen, risks + test strategy are stated, and any harness impact (new STANDARD/agent/doc) is noted. The reviewer writes a verdict + required changes into the plan; the orchestrator iterates.

**Stage 4 → 5, the plain-English layer (the non-engineer's steering wheel):** the spec contract is code-shaped (file-by-file edits, signatures, named dimensions), but approval is about *intent, not code*. So every spec **MUST open with a short plain-English block — *what this builds · what "done" means for you · what you're accepting (risks/trade-offs)*** — and that block is **presented first at the approval gate (Stage 5)**, with the file-by-file detail beneath it. The user approves the intent; the detail is there to verify against, not to decode. *(The plan `TEMPLATE.md` carries a matching section.)*

**Stage 1, the plain-English open (say why before the questions):** when Discuss begins, tell the user in plain English why you're asking before any code — *"Before any code I'll ask a few questions about who this is for and what 'good' looks like; your answers steer everything downstream."* Then ask. (The *why* of Discuss is the Stage-1 row above — this is just the user-facing opener, not a second rationale.)

**Keep the docs current — it's part of each stage (no hooks needed beyond the tree check):**
- **Implement (6):** update `claugentic-ARCHITECTURE_TREE.md` for any file add/move/remove (also hook-nudged); touch `CLAUDE.md` only for a genuinely new gotcha/command/pattern — *concisely; index, don't duplicate code*.
- **Verify (7) — honesty register:** the report says what the verify **attempted and tagged**, never what it "proved." Deterministic gates passed; reviewer sign-offs are model-upheld judgment, not a guarantee.
- **Land (8):** append a dated one-liner to `claugentic-DECISIONS.md` for non-trivial choices; update `ROADMAP.md` if scope shifted; **run the Stage-9 harvest checklist** (below) before moving on.
- **Land (8) — honesty register:** the close-out names **which gate-class passed** — the *deterministic gates* (tests, tree-check, lint/type/security) vs the *reviewer sign-offs* (the audited dimensions) — never a blanket "verified/done."
- **Land (8) — close out for the user:** after the slice lands, say it plainly — *"This one's done. Next: pick another item the same way, run `/claugentic-dev-harness:build` to keep working the backlog, or re-run `/claugentic-dev-harness:audit` for a fresh picture — you're finished when Tier 1 and Tier 2 come back empty."*
- **Retrospect (9):** promote durable learnings into the `docs/claugentic-standards/` modules (entry point: `docs/claugentic-ENGINEERING_STANDARDS.md`) / `CLAUDE.md` / agent files.

---

> ### Adopter note — reading this doc inside YOUR repo (not the harness)
> When the `claugentic-dev-harness` plugin is installed into your project, this WORKFLOW.md is a **managed copy** living in your `docs/`. So a few references below resolve to the **installed plugin**, not to files your repo must contain:
> - **The specialist agents** (`plan-reviewer`, `architect-reviewer`, `lens-reviewer`, `yagni-sentinel`, `honesty-reviewer`, …) **and the `engine/*.js` scripts** referenced throughout live **inside the installed plugin** — agents are resolved at spawn (by their namespaced id), engine scripts are run via the Workflow tool from the install path. They are **NOT** a `.claude/agents/` or `engine/` directory your repo has to provide.
> - **The Definition of Done's *deterministic gates* mean THIS project's OWN gates** — *your* lint, *your* type-check, and *your* test suite — plus the architecture-tree check. The specific commands written below (`python -m pytest`, `python scripts/check_versions_synced.py`) are the **harness's own self-test** for developing the plugin itself (version-sync checks the *plugin's* two manifests and is irrelevant to an adopter). **Substitute your project's equivalents.**
> - **The architecture-tree check is wired as a hook by `init` only when the tree-gate is enabled** — a fresh or mature-no-tree repo gets a harness-format tree and the hook; a mature repo that already has its own tree is **asked** (replace it with a harness skeleton → gate on, or keep yours → **gate off, no hook wired**). If you keep your own tree (gate off), there's no hook — run `scripts/claugentic-check_architecture_tree.py` (with `python` / `python3` / `py`) manually at Verify if you ever want a one-off check.

## Definition of Done

A slice is **done** — and may land (Stage 8) — only when **all** hold. Two groups, same bar; they differ in *who* says pass:

**Deterministic gates** (pass/fail, can't be argued around):
1. **Full test suite green** + any regression/snapshot tests — *your* project's suite. *(This plugin's own suite is `python -m pytest`.)*
2. **Architecture-tree check green** (when the gate is wired) — file-index presence, staleness, and **glob-drift detection**. Run it with whichever interpreter you have (`python` / `python3` / `py`): `scripts/claugentic-check_architecture_tree.py`.
   - **Updating the codebase map (the handled drift case).** When this gate reports *glob drift* — it's watching no files while the repo now contains source (e.g. an `init`'d empty repo that has since grown real code) — surface it to the user in plain English: *"I'm updating your codebase map to match your new code"* — then re-detect the layout and reset `INCLUDE_GLOBS` (init step 5's terminating self-correction). **Scope the claim honestly:** this is the *handled* drift path reading as plain English — a **genuine gate crash still fails loud by design** (CLAUDE.md: never swallow errors); don't promise "no error ever," only that this one case is plain.
3. **Version-sync green** — *harness-self only* (developing this plugin): `python scripts/check_versions_synced.py` enforces `plugin.json` ↔ `marketplace.json` version equality (`plugin.json` is the source of truth). A **run-gate** (like `pytest`), not hook-wired; scope is the two manifest versions only. **Not applicable to adopter repos** — skip it.
4. **Doc-budget green** — the managed ledgers (`CLAUDE.md`, `claugentic-DECISIONS.md`, `claugentic-ROADMAP.md`) stay within byte budget: `python scripts/check_doc_budgets.py`. A **run-gate** (like version-sync), not hook-wired; it emits a **WARN at ≥90%** (the cue to condense — merge superseded entries to git history) and fails only on a strict breach.
   - **Condense on WARN (model-upheld, the ledger-condensation discipline).** A ≥90% WARN is a **do-it-now signal, not a deferral** — as part of the current work, **condense that ledger**: merge superseded/settled/landed entries into git history (the archive) and keep only the still-true, forward-looking essentials a future agent actually needs. The always-/often-loaded context stays lean by design; a ledger that only grows is tech debt. No separate task — the WARN is the cue, the condense is the work.
5. **The project's lint / type-check / security gates** green.

**Reviewer sign-offs** (model judgment, not a mechanical gate):
5. **In-scope `docs/claugentic-standards/` dimensions pass** the `architect-reviewer` audit (incl. SOLID) — solo, or synthesized from `lens-reviewer`s + `yagni-sentinel` — for what this slice touches; `/simplify` + `/code-review` run.
6. **Runtime QA (dial-gated)** — when the slice's spec carries acceptance criteria (the `docs/claugentic-PRODUCT_SPEC.md` schema) and the repo records a run-the-app command, and the Verify dial has fanned out: the QA workflow (`engine/qa.js`) attempts each criterion's flow in the running app and tags the outcome (screenshots on failure); findings join the Verify synthesis. The script mechanically sequences the flow attempts and never silently skips (a QA run that could not boot the app reports exactly that); invoking it, and the flow observations themselves, are model-upheld.

**Plus:** acceptance criteria met (the spec's checklist) **+ no new tech debt.**

**The autonomy ladder (who watches the iteration to this bar):** **checkpoint** (default — the three routine pauses) → **build-to-green** (requestable; unlocked per-repo only when CI runs the deterministic gates, a test baseline covers the touched code, and the item traces to an approved spec with testable acceptance criteria — the contract, the evidence-stated checks, and the verbatim decline live in `skills/build/SKILL.md` → Mode handling; the engine — `engine/build-item.js` — is built and runs only via the Workflow tool from the installed plugin (the session precondition), so a build-to-green ask declines only when a per-repo condition is unmet or that engine is unavailable). Either rung iterates to the **same fixed bar above**; build-to-green is a reduction of unwatched-run risk, never a substitute for the unbuilt deterministic trust-gates.

Iterate to meet this **fixed** bar, then **stop** — it terminates because the bar is *finite*, not "is it perfect?". Genuinely separate future work → `ROADMAP.md` (backlog, *not* debt).

---

## Executing an audit backlog item — tag → discipline

`/claugentic-dev-harness:audit` writes a tagged backlog into `docs/claugentic-ROADMAP.md`; there is **no separate refactor command**. Each item runs through the pipeline above, and its **tag selects the discipline** the implementer applies. The tag→discipline mapping is defined here (the canonical home); the rationale is settled — don't re-litigate it.

| Tag | Discipline |
|-----|-----------|
| **`refactor`** | **Characterization-tests-first — a HARD precondition.** A refactor item on untested behavior-bearing code **cannot start until its Tier-1 "establish a test baseline" item is done**; if that baseline is absent, the implementer **stops and asks** rather than touching code. Durable enforcement will be the Trust-track `PreToolUse` characterization hook (the first Phase-1 item); **until that hook lands, this is upheld by the implementer + the Verify gate** — not yet automatic. **Say this to the user when the pause fires:** *"Before I tidy this code I need to capture what it currently does as a test, or I can't prove I didn't change its behavior — so I'll establish that baseline first."* |
| **`capability-upgrade`** | Migration safety — feature-flag the new path · dual-write / shadow-read · keep a rollback. |
| **`dependency-health`** | Update + verify — bump, run the full suite green, check the changelog for breaking changes. |
| **`bug`** | Reproduce-first — a **failing test that captures the bug**, then the fix that makes it pass. |
| **`feature`** | The standard pipeline (no extra precondition). |

---

## 9. The learning loop (how the harness grows)

A **finite harvest checklist the orchestrator RUNS at Land** (manual discipline, not automation — the orchestrator runs it; it does not trigger by itself). Sweep these six; for each, **emit the edit** or an explicit *"nothing durable this slice"*:

- **(a)** A convention that recurred across review findings → **promote to STANDARDS / CLAUDE.md**. A promoted lesson **must record the incident that motivated it** (the failure/near-miss it prevents) — so the rule is un-cargo-cultable and safe to delete once its cause is gone.
- **(b)** A manual/lens catch that a gate or checklist **could have made** → **open a gate item on `ROADMAP.md`** (not just a `claugentic-DECISIONS.md` line — a logged decision doesn't become a check by itself). *(Worked example: a manual catch that recurred at Verify — logged as a decision but never enforced — gets opened as a `ROADMAP.md` gate item so it becomes a real check, not just a note.)*
- **(c)** A prompt tweak that sharpened a specialist → **fold into the `.claude/agents/` role file**.
- **(d)** Process friction → **edit this `WORKFLOW.md`**.
- **(e)** Every non-trivial choice → **one dated line in `claugentic-DECISIONS.md`**.
- **(f)** A **load-bearing invariant** this slice established or relied on (a "must stay true or X breaks" constraint whose rationale isn't obvious from the code) → **record it in `docs/claugentic-INVARIANTS.md`** with its why + dated provenance. **Create the file lazily** — only when you have the first genuine invariant to record (do not seed it empty); most slices have none, so the honest emit is usually *"no new invariant this slice."*

**Promotion is two-tier (manual, user-approved):** a *universal* lesson → stage in `docs/claugentic-standards/CANDIDATES.md`, then promote upstream so every repo gets it on update; a *codebase-specific* lesson stays **local** (`CLAUDE.md` / `claugentic-DECISIONS.md`), never propagated.

---

## Plan file lifecycle

`​.claude/plans/NNNN-<slug>.md` (active, contains Plan + Review + Spec + slice checklist) → on completion, **remove it** — git history keeps it. One plan per substantial change; the slices inside it are the per-session units. Numbering is sequential (`0001`, `0002`, …).
