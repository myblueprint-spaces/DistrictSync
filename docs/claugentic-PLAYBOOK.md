<!-- claugentic-dev-harness@0.1.40 managed — do not edit (copied from the claugentic-dev-harness plugin) -->
# The Harness Playbook — how to drive an AI dev team

A plain-English guide to working *with* this harness, for a capable non-engineer. It turns ad-hoc "vibe coding" into a disciplined practice that gives you a real shot at software that passes a professional code review. **You don't write code — you make product calls and approve the right things at the right moments.**

## The one-minute model

Substantial work flows through a pipeline: **Triage → Discuss → Plan → Review-the-plan → Spec → Approve → Implement → Verify → Land → Retrospect** (small changes skip straight to Implement + Verify). The agent runs it; **you steer at four points.** Full version: [`WORKFLOW.md`](claugentic-WORKFLOW.md).

## Your four leverage points

The whole system is built so you steer **what gets built and what "done" means** — with product judgment. Approving the spec is the steering point; you set intent and bless the plan, and the harness carries the code-shaped detail from there:

1. **Brainstorm at Discuss.** Tell the harness *who the user is* and *what "good" means* for this product. The more context here, the better everything downstream. This is your Product-Designer seat.
2. **Approve the Spec.** Nothing gets built before you sign off on *what* will be built and *what "done" means*. You approve intent, not code. This is your steering wheel.
3. **Bless the irreversible step.** Just before anything hard to undo — committing, pushing to a shared branch, deleting — the harness **pauses and asks first.** Your judgment is load-bearing here: it never takes an irreversible action on its own. Say go, or say wait.
4. **Approve lessons.** When the harness says "I think this should become a standard," you keep or kill it. That's how the standards stay *yours*.

### How to approve a spec (you don't need to read the code)

When the agent hands you a spec to approve, read the plain-English part and ask yourself:

- **Does this match what I asked for?**
- **Is anything I care about missing?**
- **Are the risks ones I'm OK with?**
- **What does it explicitly NOT do?** (a good spec says so.)

If any answer is "no," say **"this is missing X, please revise"** — you don't have to fix it yourself. And the technical detail below the plain-English block is for the agent and reviewer to check against — **you are not expected to read it.**

Everything else — reading code, writing it, reviewing it — the harness fans out to specialists so your attention stays on decisions.

## Why it's trustworthy

Three moves make the output worth trusting:

1. **A different, skeptical agent reviews the work** and tries to refute it. The author is the worst judge of their own work, so the harness never lets the agent that wrote something be the one that signs off on it.
2. **Anything that can be checked mechanically is checked, not believed** — a test, a `grep`, a web-lookup. Models produce confident, professional-looking errors; a deterministic check doesn't care how confident the prose is.
3. **It's honest about its limits.** Where a claim *can't* be checked mechanically, the harness labels the finding as judgment, not proof — so you always know which is which.

## How to start work

**To start anything — a backlog item or a brand-new project — just tell the agent in plain English what you want** (e.g. "Let's do Tier-1 item 1" or "I want to build X"). It will ask you questions (Discuss), then write a plan and spec for you to approve before any code. That's the go-button: you describe what you want, it drives the workflow.

**If the agent starts writing code without asking you product questions first, say "use the workflow"** — it should pause and ask.

**At the start of each session the harness greets you with where-you-are / what's-next** — one derived, advisory line (any work in flight, or the single best next step). It's a suggestion to confirm, never an auto-drive, and it stays silent when there's nothing to pick up.

### A quick check of one file or a diff

You don't always need a full audit. For a fast read on a single file or a change you're about to keep, two built-in commands give a lightweight once-over: **`/code-review`** (a focused review of the current diff) and **`/simplify`** (an over-engineering / can-this-be-simpler pass). Use them when you just want a second opinion on a small piece — they don't replace `:audit` (the whole-codebase snapshot) or the full workflow, they're the quick-look option.

## Using the audit (`/claugentic-dev-harness:audit`)

Run it as a **periodic snapshot**, not a treadmill: the **engineering backlog regenerates** (it doesn't pile up — the separate **product backlog**, written by `/claugentic-dev-harness:product` gap mode, regenerates independently and is never touched by an audit), **Tier 3 is optional polish**, and an **empty Tier 1 + Tier 2 means the code is sound** — your signal to stop, not a prompt to invent work. It **auto-sizes** its effort to the repo (override with `quick` / `standard`). And it doesn't just *assert* its findings: **every surfaced finding (all tiers) gets re-checked** — a separate agent, by default on a **different model family** than the builder (the cross-model judge; a same-family run is tagged as such), reads the actual code and tries to disprove each one, so false alarms are dropped and each survivor is tagged with what came back. *(That's an honest reduction of false confidence — a reduction of shared-blind-spot risk, not an absolute guarantee.)* *(Full operating rules live in the skill's own "How to use it".)*

Once the engineering backlog is written, starting an item is the same go-button as everything else: **tell the agent "let's do Tier-1 item 1"** (or whichever) in plain English, and it runs the workflow from Discuss. To work **more than one** item, the go-button is **`/claugentic-dev-harness:build`**: name several items or a whole tier ("build all of Tier-1"), confirm the order, and it works them one by one to the honest "sound on the audited dimensions" stop-signal. **`build` reads both backlogs** — engineering and product — and presents one worklist interleaved by tier, each item tagged with which lens raised it (run `/claugentic-dev-harness:product` to populate the product backlog first if you want it folded in) — re-checking the code it just touched between items and pausing for you only when new important work surfaces (and never for anything irreversible without asking). You can approve each spec as its turn comes, or say **"spec everything first"** to plan the whole list and approve it in **one sitting** before any building begins.

## What v2 changed (the short version)

The choreography you read about above is now **executable scripts** the skills run, not just prose the agent must remember:

- **The pipelines are code.** The audit, the review panel, and the build loop ship as Workflow scripts (`engine/verify.js` · `audit.js` · `qa.js` · `build-item.js`). The honest formula: *the skill invokes the script, and the script then runs the fan-out / cross-model re-check / panel mechanically.* If the Workflow tool isn't available in a session, the skill **says so**, runs the prose path, and tags the run "prose-orchestrated" — never claiming the script guarantees. See [`WORKFLOW.md`](claugentic-WORKFLOW.md) + the skills. *(These `engine/*.js` scripts live in the **installed plugin** and are run from there via the Workflow tool — they are not files in your repo.)*
- **Autonomy is a ladder.** Default is **checkpoint** (you approve at the spec). **Build-to-green** (the loop iterates unwatched until gates + acceptance criteria pass, pausing at release) is *earned per-repo* — only with CI + a test baseline + a testable spec. It is **a reduction of unwatched-run risk, never a substitute for the unbuilt deterministic trust-gates.** See `skills/build/SKILL.md`.
- **The harness can run your app (QA).** It boots the app and drives the spec'd flows in a real browser, checking empty/loading/error states. A QA pass that **couldn't run** says so — never a silent skip. See [`WORKFLOW.md`](claugentic-WORKFLOW.md) → the QA gate.
- **A product layer.** A `product-spec` flow rebuilds `docs/claugentic-PRODUCT_SPEC.md` with testable acceptance criteria; a `product-gap` audit checks intent vs. what's built. See `skills/product/SKILL.md`.
- **A measured harness.** A release-time **drift check** re-runs an audit over a seeded-defect fixture and compares the score to a baseline — a **drift detector, not a quality guarantee**. See [`../eval/BASELINE.md`](../eval/BASELINE.md).

## When in doubt

Ask the agent to **explain what it just did and why** — it's built to teach you as you go. The goal is that you get better at directing it every cycle.

---

## Under the hood (optional — you don't need this to drive it)

**The patterns that produce quality.** These are *how* the harness aims past "standard practice":

- **Fan-out** — many specialists in parallel, each on one piece (speed + focus).
- **Author → adversarial verify** — one agent writes; a *different, skeptical* agent tries to **refute** it. The model that wrote something is the worst judge of it. *This is the core trust move.*
- **Trust the oracle, not the model** — wherever a fact can be checked mechanically (a test, a `grep`, a web-lookup), check it that way. Models produce confident, professional-looking errors; deterministic checks don't.
- **Effort dial** — match review depth to the change's risk; don't run the whole machine on a typo.
- **Diverse panel + cross-model judge** — a contested or trust-touching change is critiqued by several *different* lenses at once (a skeptic, an over-claim checker, a product eye), and the gating judges run by default on a **different model family** than the builder — fewer shared blind spots than one reviewer, one model.
- **Judge-panel / best-of-N** — for a big design fork, generate several approaches, score them, combine the best of each.

**A few terms you'll see.**

- **Characterization / golden-master test** — a test that captures what the code *currently does*, so you can change it and prove the behavior didn't move. The safety net for touching legacy code.
- **Idempotent** — doing it twice is the same as doing it once (safe to retry).
- **Gate / fitness function** — an automatic check that *enforces* a standard; it can't be argued around.
- **Lens** — one quality viewpoint (security, performance, UX…); the harness reviews through several at once.
- **Dual-layer output** — every finding stated technically *and* in plain English ("what this means for you").
- **Slice** — one unit of work small enough to finish completely in a single session, with no half-done leftovers.
