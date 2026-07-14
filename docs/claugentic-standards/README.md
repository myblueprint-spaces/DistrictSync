<!-- claugentic-dev-harness@0.3.0 managed — do not edit (copied from the claugentic-dev-harness plugin) -->
# Engineering Standards — Catalog

The multi-lens quality bar, as **scoped modules**. A module loads only when a change
touches its concern (see each module's `load_scope`), so the catalog can grow toward
"every standard we can think of" **without bloating any single review**. Anchored to
**ISO/IEC 25010:2023**.

- **Entry point:** `docs/claugentic-ENGINEERING_STANDARDS.md` (thin — points here).
- **Module contract:** every module copies `_TEMPLATE.md`.
- **Who uses it:** the spec (Stage 4) names the in-scope modules/dimensions; `implementer` builds to them; `synthesizer-gate` audits against them (see `docs/claugentic-WORKFLOW.md` → Definition of Done).

## How to use this catalog (meta-rules)

- **Select, don't skip.** For a given change, the architect picks the dimensions that are *relevant* and meets each one **fully** — no debt. Don't gold-plate irrelevant dimensions (that's its own waste — respect `KISS`/`YAGNI`), but **never skip a relevant one.** Relevance is a per-change judgment.
- **`load_scope.globs` is an advisory relevance HINT, not a gate.** Each module's `load_scope.globs` (often defaulting to `src/**`) just suggests which changed files pull the module in; the `lens-reviewer` is told its module explicitly when invoked, so a non-matching default (e.g. a repo whose code isn't under `src/`) does **not** break anything or silently drop the lens — it's a hint to be refined per repo, never a hard filter.
- **No hard "N/A" caps in the dimensions.** Don't mark a dimension *permanently* irrelevant — a stack grows into things, and a cap would mislead a future agent. A repo's *current* applicability is captured in a **Current scope** section that the `init` skill **seeds per-repo in the adopter's `CLAUDE.md` `harness:` section** (a local, non-managed spot — a non-capping, growing snapshot of which dimensions are live in that codebase today); this plugin ships the global catch-all only and does **not** ship that section populated. Ultimate relevance is always a per-change judgment.
- **Additive, not subtractive.** You may **add** dimensions/standards as you discover them; **don't remove** existing ones. This is meant to become "every standard we can think of."
- **Not confined — to this list or to known patterns.** Exceed the list when a change warrants it. Prefer established design patterns, but you **may invent a novel pattern** when it adds clear value — justify the problem, why existing patterns fall short, and the benefit, and record it in `claugentic-DECISIONS.md`. Unconventional ≠ wrong.
- **The spec names the in-scope dimensions.** Stage 4 records which dimensions apply to a slice and the target bar; Stage 7 audits against them; "done" = they pass (see **Definition of Done** in `claugentic-WORKFLOW.md`).

## Two-tier knowledge: global (synced) vs local (stays put)

Standards are **copied into each adopting repo on init**, not read from the plugin at runtime (copy-on-init — see `claugentic-DECISIONS.md` → "Copy standards on init").

- **Global modules — this directory.** Universal standards. They are **bundled in the plugin** (the source of truth) and **copied by the `init` skill** into the adopter's local `docs/claugentic-standards/`, version-stamped and headed **"managed — do not edit."** Agents read the **local copy**. They are **pristine**: a hand-edit inside an adopting repo is lost whenever a newer plugin version's copy replaces it — **never hand-edit a managed copy.** **Which one you're looking at depends on the repo:** in the **`claugentic-dev-harness` plugin repo** these modules ARE the editable source (no stamp — edit them here); in an **adopter repo** they are **managed copies** (version-stamped, overwritten on re-init) — to change a standard, edit the **plugin**, not the copy, and re-init to propagate.
- **Local artifacts — the adopting repo (`${CLAUDE_PROJECT_DIR}`).** The **Current scope** snapshot (which dimensions are live in this repo), `CANDIDATES.md` (lessons awaiting promotion — a local buffer **created on first use**, not shipped empty), and repo lessons in `CLAUDE.md` / `claugentic-DECISIONS.md`. These **never propagate** to other repos.
- **Promotion path.** A lesson that's *universal* is staged in `CANDIDATES.md` (born the first time you stage a lesson there), reviewed, then promoted upstream into a global module (with a version bump) — so every repo gets it on its next plugin update. A lesson that's *repo-specific* stays local. This is the two-tier learning loop; the promotion is manual (see `docs/claugentic-WORKFLOW.md` → learning loop).

## Versioning

- Each module is **semver**-versioned in its frontmatter; bump on any content change (patch = fix/clarify · minor = add a dimension · major = restructure).
- Newer plugin versions carry updated **global** modules (the version stamp records which release a local copy came from); **local** artifacts are never touched.

## Module index

Each module's status lives in its own frontmatter — **all are currently `draft`** — read it there, so this index can't drift.

### Authored modules

| Module | ISO/IEC 25010 |
|---|---|
| `security` | Security |
| `maintainability-structure` | Maintainability |
| `testing` | Maintainability · Reliability · Functional-suitability |
| `product-ux` | Interaction Capability |
| `data-and-persistence` | Reliability · Maintainability |
| `reliability-resilience` | Reliability · Safety |
| `performance-efficiency` | Performance Efficiency |
| `observability-ops` | Reliability |
| `api-and-contracts` | Compatibility |
| `internationalization` | Interaction Capability |
| `docs-traceability` | Maintainability |

> ⚠️ **Before trusting a citation.** A `draft` module's citations are **model-asserted** — a starting point, not confirmed fact. They are independently checked when the module is promoted to `stable` or pulled into real work. Treat a draft module's specific source references accordingly.

### Reserved — named, not yet authored

| Module | ISO/IEC 25010 | Status |
|---|---|---|
| `architecture-styles` *(reserved)* | Flexibility | reserved — no file yet (authored when a change pulls it in) |
| `capabilities/` *(reserved)* — Redis, queues, object-storage, third-party-apis, sidecars, ml, search | (various) | reserved — no files yet (authored just-in-time when an audit pulls one in) |

> **Status legend** (per `_TEMPLATE.md`): `stub` = listed, unwritten · `draft` = written, citations model-asserted, not yet battle-tested · `stable` = dogfooded. (`reserved` rows above are named but have no file yet.)
>
> **`[D]` vs `[J]` — what the audit can actually prove.** A `[D]` (deterministic) check is only **proven** when the adopter has the relevant tool wired (linter / scanner / test runner / CI). Without that tool present, the audit can't run it — it falls back to reporting that check as the **model's judgment (`[J]`)**, not a verified fact. So a module's `[D]` tags describe what's *provable in principle*; what's *actually proven* on a given repo depends on its tooling.
