# Charter — the approach this project has settled on, per KIND of work

A living, per-work-**TYPE** record. It exists so agents and sessions stay consistent
without re-deriving the same judgment every time, and so the harness never collapses into
one golden hammer.

> **Provenance and version skew — read before citing this file.** The apply/record/adapt/
> grow model comes from **claugentic-dev-harness 0.4.1** (the INSTALLED plugin), whose
> WORKFLOW carries a *methodology toolbox* section. **This repo's managed
> `docs/claugentic-WORKFLOW.md` is stamped `@0.3.0` and contains no such section**, so do
> not cite it for the rules below — it does not contain them. This file is therefore
> **adopted ahead of the repo's harness stamp**: it is a 0.4.1 practice living in a 0.3.0
> checkout, and it will reconcile when the managed docs are refreshed by a re-`init`.
> (The version skew itself is a known standing item — the repo stamps 0.3.0 against an
> installed 0.4.1; re-running `init` is the owner's call.)

**How to use it** (per the installed harness 0.4.1's *methodology toolbox*, NOT per this
repo's `@0.3.0` WORKFLOW.md — see the provenance note above):

- **APPLY** — an entry exists for this kind of work → continue with the recorded approach.
- **RECORD** — the first time a kind of work is judged → add a
  `work-type → approach + one-line rationale` row.
- **ADAPT** — genuinely different work-nature → do NOT force it into a recorded approach;
  pick what fits and record THAT for its type.
- **GROW** — a recorded approach proved wrong for its type → update the entry **in place**
  (an entry is a *revisable default*, never a mandate).

An **absent entry means the harness's default grain**, not "anything goes".

---

## Entries

### Pure predicate / primitive modules whose semantics the spec DECIDES
_Recorded 2026-07-29 (plan 0038 S3 — `src/utils/identity.py`, `validators.validate_identity_email`, `ui_flet/identity_gate.py`)._

**Approach: RED-FIRST.** Write the edge table first, watch it fail, then implement — and
follow with perturb-and-restore probes on every decision the table encodes.

*Rationale:* when the acceptance criteria are fully determined by the spec (normalise how?
keep the plus tag? which `@` wins?), a test written afterwards tends to describe the
implementation rather than the requirement. Writing it first forces each judgment call to
be stated as a DECISION before any code can quietly make it.

*Independence caveat, recorded honestly:* the strongest form of this is an **independent
test-author spawn** (clean context, given only the spec, never the solution) followed by
greening without editing the test files. When no sub-agent tool is available, the tests are
self-authored — which is genuinely weaker, must be said so, and should be compensated with
falsification (perturb each guard, observe red, restore) rather than claimed as independent.

### Config-schema keys, shipped DATA rows, and repo-hygiene gates
_Recorded 2026-07-29 (plan 0038 S3 — `district_domains` + its validator, the six domain rows, `scripts/check_no_emails.py`)._

**Approach: SURVEY → DESIGN → TEST-ALONGSIDE, then perturb the DATA, not just the rule.**

*Rationale:* the test cannot precede the design here, because the design IS the decision —
a scanner's allowlist could not be authored before the survey that revealed what the repo
actually contains, and a validator's shape follows from the values it must accept. Test-first
would only encode a guess. The compensating discipline is where the perturbations are AIMED:
a validator-only probe leaves the shipped rows untested, so at least half the probes target
the data (a row on the base config, two districts claiming one domain, a value re-derived
from the wrong source, a deleted row).

### Documentation + its enforcing gate
_Recorded 2026-07-29 (plan 0038 S2b — `docs/developer/output-contract.md` + its drift test)._

**Approach: author the doc, then write the gate against it, then falsify every gate
assertion by perturb-and-restore.**

*Rationale:* red-first does not apply when the doc IS the artifact and the test is only its
gate. What matters instead is that the gate has teeth — and that at least one perturbation
is aimed at the space the gate does NOT cover, since perturbation evidence inherits the
perturber's blind spots (see `docs/claugentic-standards/CANDIDATES.md`, 2026-07-29).
