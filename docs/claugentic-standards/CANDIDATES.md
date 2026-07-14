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
