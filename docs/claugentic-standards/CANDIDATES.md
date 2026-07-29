# Standards candidates — staged for upstream promotion

> Universal lessons harvested from this repo's programs, staged here because the
> per-dimension modules in this directory are managed plugin copies (do not edit).
> Promote into the upstream claugentic-dev-harness standards/roles, then remove.

## product-ux — "Honest system-status copy (trust surfaces)"  [staged 2026-07-08, plan 0029]

Good looks like — every success/status string names WHAT was checked and WHEN
(host+user+credential-source for a connection test; OS read-back for a schedule)
and never asserts a state it did not verify. Named failure modes to refute:

- **hint-as-truth** — a stored config value (schedule_time, sis_type) rendered as if freshly verified
- **assert-unchecked-state** — "nothing was changed" / "it works" before a read-back confirms it
- **confirm-vs-fail headline split** — "Couldn't confirm" (timeout/no-result) must not read as "Couldn't register" (declined/error)
- **record-gap contradiction** — a "fired but didn't complete" claim must rest on evidence (a real record gap), never a benign non-zero code
- **adaptive finish copy** — a summary's headline must match the actually-achieved state (skipped ≠ "all set")
- **persisted-vs-transient discriminator** — a "will happen" claim must key off PERSISTED + reconciled state, never a transient in-session success (0029 F1: TESTED_OK claimed delivery a task without `--sftp` would never make)

Incident: 0029 trust slices — 4 SFTP over-claims (S7), a timeout asserting the unknowable (S6),
a config-hint next-run (S5), an over-signalling finish (S8), a transient-keyed delivery claim
(whole-program F1) — each caught only by the honesty lens / closing pass, not by any gate.
Beneficiary role: `honesty-reviewer` (name these patterns when refuting copy).

## testing — "Isolation via patched deep seams + canary tripwire"  [staged 2026-07-08, plan 0029]

Good looks like — global side-effect paths (user data dir, log file, DB) resolve through ONE
deep seam at CALL time; the autouse fixture patches THAT seam, not a shallow alias a module
already imported by reference (which patches nothing). A canary test exercises the real
side-effects under the fixture and asserts the real profile is byte-untouched. Know the leak
class: module/class/session-scoped fixtures execute BEFORE function-scoped autouse isolation —
they must redirect the seam in their own setup or they write the real artifact.

Incident: 0029 Slice 4a/4b — patching `user_log_file` was a no-op (`logger.py` by-reference
import); AppConfig bypassed `paths.py`; SD74/contract module-scoped fixtures leaked the real
`history.db` past the autouse fixture (the canary caught it). Confidence — a function-scoped
canary is a tripwire, not a proof: it catches an alphabetically-earlier leak only; a
session-teardown assertion catches all orders.

## verify — "Whole-feature closing pass earns its keep"  [staged 2026-07-08, plan 0029]

Evidence for the WORKFLOW's Stage-7 closing pass: 12 slices each passed adversarial per-slice
verify (solo gate / gate+honesty / gate+security+honesty, every fanned-out lens catching real
findings) — and the ASSEMBLED program still carried a shipping-grade cross-slice bug (the
wizard registered the nightly task before delivery was configured; no seam re-registered).
Only the persona-journey walk through the assembled code found it. Composition seams between
slices deserve their own regression tests (register→enable-delivery→assert the task action).

## testing — "Vacuous-green protection: pair the absence-assertion, pin the copied literal"  [staged 2026-07-29, plan 0038 S1]

Good looks like — a check can only go green for the reason it claims. Two named failure
modes, both of which pass while proving nothing:

- **unpaired absence-assertion** — "X was NOT created / NOT changed / NOT logged" is satisfied
  both by the guard working and by the mechanism *never running at all*. Every absence-assertion
  needs a POSITIVE twin in the same suite, ordered so the twin is unambiguous (pin absent first,
  then prove the same mechanism creates it).
- **orphaned copied literal** — a marker/constant duplicated out of the code that emits it (a
  standalone script that must not import the app, a CI list, a doc table) silently rots into an
  assertion about a string nothing produces. Where the duplicate is genuinely unavoidable, a
  PARITY test driven off ONE shared value table is the single source — never a comment asking a
  human to keep two places in step.
- Corollary: a **skip** on a missing precondition is a vacuous pass. Refuse once, up front,
  before any phase runs — don't skip per-check.

Incident: plan 0038 S1 (2026-07-29), the exe CLI smokes. `--dry-run` writes no run record, so the
smoke asserted `history.db` untouched — which on a fresh CI profile also passes if the store never
writes at all; `write-run` gained the positive twin and the phase order became load-bearing and
single-sourced in `CLI_SMOKE_PHASES`. The same script copies four log markers and the
`DISTRICTSYNC_DATA_DIR` parsing rule out of `src/` (it must stay import-free); each is now pinned
by a parity test. Prior art in-repo: an isolation canary watching an abandoned path, an exit-code
test asserting `sys.exit(3)` exits 3, a profile smoke vacuous twice over (platformdirs ignores
`LOCALAPPDATA`). Beneficiary roles: `lens-reviewer` (testing), `implementer`.

## role: implementer — "An acceptance criterion that names an ARTIFACT is not met by a stand-in"  [staged 2026-07-29, plan 0038 S1]

Prompt line to fold in — when a criterion names the built/packed/deployed ARTIFACT (the exe, the
image, the bundle), exercising the same source another way (`python -m src.main`, the unit suite,
a dev server) is NOT evidence: it proves the code, not the packaging — and packaging is exactly
what the criterion doubts. If the artifact cannot be produced in-session, report the criterion as
**CI-pending, naming the run that will decide it** — never as met.

Incident: plan 0038 S1 — the implementer validated the four exe smokes with `python -m src.main`,
satisfying the criterion's letter and skipping its point; the verify panel caught it and the
smokes ran against the frozen artifact before the land record claimed them.

## workflow — Land/Verify process lessons  [staged 2026-07-29, plan 0038 S1 — WORKFLOW.md is a managed copy, so staged here]

- **A rollback rule is a COUNTER-COMMIT, not a revert.** "If X is red, `git revert <commit>`" is
  valid only while that commit's lines are untouched — a verify batch routinely edits the prose
  that commit introduced (corrections that *should* survive a rollback). Write the rule as the
  exact lines to flip. (Provenance: the light-flavor revert became impossible mid-slice and was
  restated as a 3-line counter-commit.)
- **An interrupted panel: an absent lens is an ABSENT verdict, never a CLEAN.** A fan-out that
  dies part-way (usage limits, an outage) is re-run for the missing lenses — pinned to the same
  model tier so the re-run is comparable — and the synthesis names which lenses actually returned.
  (Provenance: a mid-panel usage-limit outage, recovered by re-running the missing lenses
  model-pinned via a workflow.)
- **[OWNER DECISION PENDING] Panel discharge of /simplify + /code-review.** Proposed rule: when
  the fan-out panel ran, yagni-sentinel's explicit simplification pass + the lens panel discharge
  `/simplify` and `/code-review` (say so in the verify record rather than running both twice); a
  solo synthesizer-gate verify still runs them. Loosens a stated Stage-7 instruction — needs the
  owner's nod before promotion.
