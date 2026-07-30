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

## testing — "Prove a new assertion's teeth by breaking it; declare coverage gaps, never absorb them"  [staged 2026-07-29, plan 0038 S2a]

Two disciplines from a test-only slice, both cheap and both catching what review alone missed:

- **Perturb-and-restore evidence.** A new assertion class ships with a falsification table — each
  oracle deliberately broken (a swapped column, a shrunk expectation set, an inverted byte check),
  the red observed, then restored. "The test passes" says nothing; "the test failed for the right
  reason when I broke the thing it guards" is evidence. Pairs with the vacuous-green lesson above.
- **A declared-gap table beats an implicit gap.** When an expectation table legitimately diverges
  from derived truth (a fixture deliberately omits sources), the divergence lives in an explicit
  reviewed constant (`DELIBERATELY_UNCOVERED = {...}`) asserted per-config in both directions — so
  "fixing" a red test by shrinking a frozenset fails the guard, and a new config's uncovered
  entity surfaces as a red, not silence.

Incident: plan 0038 S2a — EXPECTED_ENTITIES' keys were pinned but its values were "true because
typed"; the gate demanded the declared-gap guard, and the falsification table (6 perturbations)
was what let the panel accept a hand-maintained table over a derived one without eroding the
sd51 skip-on-empty pin. Beneficiary roles: `implementer`, `lens-reviewer` (testing).

## testing — "Perturbation evidence inherits the perturber's blind spots"  [staged 2026-07-29, plan 0038 S2b]

Perturb-and-restore proves an assertion has teeth ONLY where a perturbation was aimed. Two
incident shapes from one slice: (a) the doc-wide-stamp hazard was perturbed only INSIDE the
gated tables, so the assertion that "the confirmed stamp cannot spread" was green while a
front-matter re-stamp — the exact hazard — passed untouched; (b) a vacuous perturbation
(replace-first of a twice-occurring string) was caught and fixed on the PERTURBATION side,
leaving the weak ASSERTION in place. Rules: aim at least one perturbation at the space the
assertion does NOT cover (the ungated region is where the hazard lives); when a perturbation
goes vacuously green, fix the assertion, not the probe. The verify panel's job includes
red-teaming the falsification evidence itself. Beneficiary roles: `implementer`,
`lens-reviewer` (testing), `synthesizer-gate`.

## security/testing — S3 lessons: value-validated choke points, reality-read pins, tense discipline, is-text filters  [staged 2026-07-29, plan 0038 S3]

- **A choke point validates VALUES, not just keys — and applies all-or-nothing.** `identity_save`
  gated on `hasattr` and typed `**updates: object`: a `None` value round-tripped to a corrupted
  (UNREADABLE) settings file and a mistyped kwarg silently shadowed the method itself. Membership
  from `fields()`, per-value `_value_fits`, and deferred `setattr` until every pair passes. The
  panel live-reproduced all three failures before the fix.
- **A list written from memory twice is a list needing a reality-read pin.** The extra="forbid"
  model roster was written down wrong in S2b's doc, corrected by that panel, then written wrong
  AGAIN in S3's comment. The fix is a test that enumerates the real `model_config`s in both
  directions — prose lists of code facts rot; tests that read the code do not.
- **Dark-shipped machinery needs tense markers on every durable surface.** S3 shipped keys and
  predicates with zero consumers; ten surfaces described them in the present tense ("scopes the
  pickers"). Commit messages were honest; the durable artifacts were not. Rule: "will … once X
  lands (plan N)" until the consumer exists.
- **Bulk text operations take an is-text filter first.** A line-ending normalizer over
  `git ls-files` stripped CRLF from five binary assets (.ico/.png); caught and restored byte-clean
  pre-commit. Never run a text transform over a tracked-file list without filtering binaries.

## testing — "A string reused across surfaces stops being a usable test proxy"  [staged 2026-07-29, plan 0038 S4b]

DRY on user-facing COPY is right (two wordings of one fact is how surfaces start to disagree)
— but every existing assertion that used that string as a stand-in for "which surface am I
looking at?" silently becomes ambiguous the moment a second surface adopts it. Re-ground those
assertions on STRUCTURE (a control only one surface has, plus one only the other has — both
halves, so neither can answer alone). The tell is a previously-passing test going red for a
reason that is not a regression; treat it as the guard telling you the proxy was weak, not as
noise to route around.

Incident: plan 0038 S4b — the Home identity card imported the launch page's headline
verbatim, and `test_..._never_sees_the_launch_page` went red because the card it exists to
prove had appeared. Beneficiary roles: `implementer`, `lens-reviewer` (testing).

## testing — "A pass-through is pinned at the SUPPLY, not at the forwarding — and an absence must be pinned STRUCTURALLY when behaviour cannot see it"  [staged 2026-07-29, plan 0038 S6]

Two blind spots a falsification pass found in one slice, both invisible to a full green suite:

- **The forwarded-`None` hole.** A test that asserts "B passes `callback` on to C" stays green
  when A stops supplying one — a forwarded `None` forwards perfectly. Every hand-off in a chain
  needs the SUPPLY pinned separately, and pinned by EFFECT (fire it, observe the work it causes)
  rather than by presence, since `callable(x)` is satisfied by a callback wired to nothing.
  Incident: the shell's `on_schedule_changed=` line into Home was deletable with the whole suite
  green, because the only assertion lived one level down in Home.
- **A structural guarantee needs a structural assertion.** When a refactor's guarantee is *the
  absence of an input* ("this factory can no longer vary by state"), every behavioural test stays
  green while the input is quietly re-admitted as an unused defaulted parameter — the door stands
  open and nothing walks through it *yet*. Assert the signature (`inspect.signature`), because the
  absence IS the guarantee. Same family as the vacuous-green rule, one level up: not "did the
  mechanism run?" but "can the hazard be represented at all?".

Both were fixed on the ASSERTION side and re-probed RED (per the S2b lesson: when a perturbation
goes green, fix the assertion, not the probe). Beneficiary roles: `implementer`,
`lens-reviewer` (testing), `synthesizer-gate`.

## verify — "A correction that APPENDS leaves the falsehood standing; a reroute inherits the destination's claims"  [staged 2026-07-30, plan 0038 S6 discharge round]

S6 needed TWO adversarial rounds. The Stage-7 panel found three blockers; the fix batch for them
introduced two more, and only a second panel — a *discharge check* re-reading the CURRENT text
rather than the diff hunk — caught them. Four rules, all cheap, all earned:

- **Correct the sentence that is wrong, do not annotate around it.** Told that a `nav.py` docstring
  claim was false, the fix appended the correct fact ten lines below and left the false clause in
  place: one docstring asserting P and NOT-P, with a reader of the first half getting the falsehood.
  A review naming a FALSE sentence is discharged by deleting or rewriting *that* sentence.
- **Verify the commit message like any other published claim.** The same batch listed the untouched
  line among the claims "corrected". A commit message is a durable assertion about what was done.
- **A reroute inherits the destination's claims.** Closing "don't tell an admin their run history is
  safe when the store would not open" by routing that case to an EXISTING branch imported that
  branch's copy — "everything you've already entered is safe" — for an install the code had just
  positively checked had entered nothing. When a fix moves a case rather than adding one, re-check
  the destination against the FULL cross-product of its inputs. Stating a rule as an invariant
  ("never name an artefact you know is absent") means sweeping every branch, not the one under repair.
- **A discharge check is not the same review again.** Re-read the tree at HEAD, sweep the repo for
  the whole defect CLASS (the fix batch found three more stale quotes than the panel had listed),
  and mark each obligation DISCHARGED / PARTIAL / NOT DISCHARGED with evidence. Two of the four
  lenses independently re-ran the perturbations rather than trusting the reported numbers.

Also: the panel was wrong twice and the implementer was right both times (a control-flow premise,
and a proposed `finally` that would have deleted a double-press guard). A fix batch must be free to
refute an item with evidence rather than apply a bad fix — and the verdict should adjudicate, not
rubber-stamp. Beneficiary roles: `synthesizer-gate`, `honesty-reviewer`, `implementer`.

## workflow — "Green locally is not green; and perturbing reviewers must not share a worktree"  [staged 2026-07-30, plan 0038 S6]

- **`main` was RED for three consecutive pushes and no slice noticed.** A test added in S4b failed on
  every Linux CI run — the code path it exercised returns early on a platform without OS scheduler
  read-back, so the row tripped its own vacuity guard. S4b and S5 both merged over it, because every
  slice ran its gates on Windows, read "all green", and treated the multi-OS gate as a formality.
  **Reading `gh pr checks` output is a REQUIRED step of Land, not a habit** — and a "last CI run on
  `main` is not green" signal belongs in `doctor`. Corollary: a platform-divergent branch deserves a
  capability seam so it is exercised on every OS, not a `skipif` that hides the gap where it matters.
- **Isolate any reviewer that perturbs source.** A four-lens panel was pointed at ONE worktree and
  told it was read-only, but three lenses needed perturb-and-restore to do their job; one ran
  `git checkout --` over another's in-flight probe. That can restore a file mid-perturbation and
  turn a green suite into a FALSE DISCHARGE. Give perturbing agents their own copy
  (`git archive HEAD` into scratch, or a worktree) — a brief asking for restraint does not provide
  isolation when the task requires mutation. One verifier did this unprompted; its results were the
  trustworthy ones.
- **The scratch-profile rule covers ad-hoc scripts, not just the app.** A lens ran a debug script
  from outside `tests/`, so the autouse isolation fixture did not apply and a store read resolved
  against the REAL user profile, printing real run records into tool output. The pytest fixture only
  protects code run under pytest from inside `tests/`; any process importing the profile/store
  modules needs the env override. Read-only here, but it is a privacy seam.

Beneficiary roles: orchestrator (Land sequence + panel design), `doctor`, `lens-reviewer`.

## reliability — "Gate destructive cleanup on its subject existing; latch success AFTER the risky work"  [staged 2026-07-29, plan 0038 S4a]

- A destructive side-step (purging recovery snapshots on an erasure path) must be gated on the
  primary erasure having had something to erase — an empty Save on a fresh install must be a
  no-op, not a purge. And an operation that ATTEMPTS deletions reports its outcome tri-state
  (none / all / N remaining) — a fixed success note shown on every branch claims deletions that
  may not have happened.
- A one-shot entry latch (`entered = True`) set BEFORE the risky work turns any transient failure
  into permanently dead buttons; set it only on success (or reset-and-re-raise) so the failure
  REPEATS loudly instead of going quiet.

Incident: plan 0038 S4a — both live-reproduced by the panel through the real control tree; the
purge would have destroyed exactly the upgrading population's `config.corrupt-*.json` snapshots.
Beneficiary roles: `implementer`, `lens-reviewer` (reliability).
