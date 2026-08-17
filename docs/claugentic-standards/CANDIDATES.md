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

**OWNER DECIDED 2026-07-30 — BOTH remedies, not one.** (1) The Land gate is now stated in this repo's `CLAUDE.md` Development Workflow section: a slice is not landed until CI's own result has been READ and REPORTED (`gh pr checks --watch` / `gh run watch --exit-status`), quoted rather than assumed. (2) `doctor` should gain a **"`main`'s last CI run is not green"** signal — it is a managed plugin skill, so it is requested here rather than edited: the check is one `gh run list --branch main --workflow ci.yml --limit 1` call, and it catches the case the Land gate cannot (a landing that skipped or shortcut the sequence, or a push straight to `main`). The two are deliberately redundant because they fail independently.

Beneficiary roles: orchestrator (Land sequence + panel design), `doctor`, `lens-reviewer`.

## verify — S7 lessons: the fix batch is where the next false claim gets written  [staged 2026-07-31, plan 0038 S7]

S7 needed TWO adversarial rounds and produced six blockers between them. Every durable lesson below was
paid for twice in this program — once in S6, once here — which is what promotes them from incident to rule.

## 1. A fix batch closing an over-claim is the MOST likely place to author a new one — budget a discharge round
**Incidents: S6 round 1 AND S7 round 1. Twice in one program, same shape.**
- S6: closing "don't call an unreadable run store *safe*" rerouted the case onto "everything you've
  already entered is safe" — shown to an install the code had just checked had entered nothing.
- S7: closing "you struck out a live defect on the false premise that Home renders no counts" produced a
  restatement built on **the same false premise**, propagated to TEN sites including `CLAUDE.md`, while
  two green tests asserted Home renders `It included 8,140 attendance rows.`
**Rule.** A batch whose subject is a false claim is operating in exactly the register where false claims
are easy to write. Treat "fix batch on a copy/claims blocker" as automatically warranting a discharge
round; do not treat the blocker list as the finish line. And when a batch RESTATES something it was told
was false, re-derive the restatement from the code — do not edit the sentence.

## 2. A struck-through backlog line is one nobody re-raises
**Incident (S7 BLOCK-3).** A live Run History defect was marked `~~struck~~ — DISSOLVED (not fixed) by
plan 0038 S7` on a premise that was false. Strikethrough reads as *settled* to every future reader, so a
wrong discharge in a backlog is far more durable than a wrong sentence in a docstring — nobody re-opens
it to check.
**Rule.** A discharge claim in ROADMAP is a CLAIM and needs the same bar as a confirmed contract row:
name what actually changed, and keep any symptom the change did not touch OPEN and unstruck. Prefer
"reduced, not resolved — here is what remains" over strikethrough whenever a change is partial.

## 3. A prescribed fix can have a hole — verify the OUTCOME, not the prescription
**Incident (S7 BLOCK-2).** The panel prescribed: gate `_FIRST_SYNC_LEAD` on a positive schedule signal.
The implementer did exactly that, correctly. But `_expects_a_nightly` admits `schedule_registered`, which
is precisely what the ≤v3.4.0 upgrader has — so the falsehood the blocker was raised on survived in 2 of
4 schedule states, with the module contradicting itself three lines above the offending arm.
**Rule.** Implementing a review's prescription literally is correct behaviour and must not be penalised.
The obligation on the *verifier* is to re-check the STATE the blocker named, not to confirm the
prescribed edit was made. Write blockers as "state X must no longer render Y", with the suggested fix
labelled as a suggestion.

## 4. A volunteered in-class fix still needs a pin
**Incident (S7).** The batch swept a defect class beyond its brief (good — CANDIDATES mandates it) and
fixed a twin over-claim in `run_history.py`. Then wrote three documents saying the class was closed. The
fix was reverted in a probe and **400 targeted tests stayed green**, and the sweep had in fact stopped
one arm short of a sibling that still carried the over-claim.
**Rule.** Sweeping the class is right; *claiming* the class is closed is a verifiable assertion. Any
volunteered fix needs (a) a positive assertion that bites, and (b) an actual enumeration of the class
before any document says it is swept. An unpinned fix plus a "class closed" sentence is worse than
neither — it retires the reader's suspicion without retiring the defect.

## 5. Guard the number, not the config: an entity vocabulary must come from the run that produced the counts
**Incident (S7 BLOCK-1, the slice's most serious defect).** The size clause resolved its entity
vocabulary from the district saved NOW and applied it to a record produced by whatever ran THEN,
rendering `It included 0 students.` under a GREEN "Your roster is up to date" band — the exact string
the acceptance criterion forbade, and the exact failure the feature existed to prevent. Two SHIPPED
paths produce the divergence (Mapping switches `sis_type` without re-registering the task; Convert
records a per-run district without saving).
**Rule.** Whenever a view interprets a stored RECORD using CURRENT config, the record's own provenance
field is the authority — and if the two disagree, say nothing rather than compute a number. Generalise:
*data rendered from a historical record must be interpreted with that record's metadata, never with
today's settings.* Related: an AC phrased about the CONFIG ("a config that does not emit Students is
never counted in students") was **literally satisfied by the buggy render** — phrase acceptance criteria
about the run that produced the data, not about the active configuration.

## 6. `isinstance`-filtered harvests silently exclude whole shapes
**Incident (S7 SHOULD 4).** The doc copy-parity test harvests module constants by prefix with
`isinstance(value, str)`. `SIZE_NOUNS` is a **dict**, so it is structurally unreachable by the pin — yet
its nouns are quoted word-for-word in three docs and an AC. Renaming one reddens unit rows but never the
doc side. The same file's own docstring says an undeclared gap IS the defect.
**Rule.** A reflective harvest's FILTER is part of its coverage claim. State what shape it can see, and
put anything it structurally cannot reach on the declared-gap list — a prefix pin that silently skips
every non-`str` container is an absorbed gap wearing a pin's clothes.


---

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


---

## design — "A flag's PRESENCE check and its RESOLVED value are not interchangeable"  [staged 2026-08-13, plan 0042 slice 1b]

- Once a derived value can come from **more than one input**, every gate keyed to the *presence
  of the original input* is **silently dead on the new path** — and it is dead exactly where the
  new path was introduced to make something safe, which is the worst possible place. The rule is
  to gate on the **resolved value** (`scope is not None`), never on `config.get("the_key")`, and
  never on truthiness when an empty value is itself meaningful.
- The tell that a codebase is one refactor from this defect: a resolver that already returns
  `T | None` while its consumers re-read the config key instead of the resolver's return.
- It is a **design** rule, not a test-hygiene one — but it needs a test on the SECOND path, because
  the first path makes presence and value coincide, so every existing test stays green.

Incident: plan 0042 slice 1b's inherited class bound. `resolve_timetable_scope` gained a second
source (`student_rostering_grades − homeroom_grades`, used when `class_rostering_grades` is absent).
A blend-suppression gate written the obvious way — `if global_config.get("class_rostering_grades")`
— would have left studentless `BLENDED_` classes (a teacher, zero students) alive for grades a
district is not licensed to send, raising no quality warning and no anomaly, on precisely the path
the bound exists to make total. Slice 1a's code happened to spell the gate correctly; nothing
pinned it, and nothing had told anyone the spelling was load-bearing. Now recorded in
`docs/claugentic-INVARIANTS.md` and pinned two-sided over the SD74 corpus.
Beneficiary roles: `implementer-architect`, `plan-reviewer`, `architect-reviewer`.

---

## testing/design — "Two filters that must agree need a shared ROW SET and NULL POLICY, not a shared constant"  [staged 2026-08-14, plan 0043 slice 2]

- Hoisting a shared vocabulary (a set of valid codes, an enum, an allowlist) into one module makes
  two call sites agree about **which values** count. It does **nothing** to make them agree about
  **which rows** they look at, or what they do with the blanks. A reviewer who sees the DRY hoist
  will read the disagreement as impossible; it is not.
- The tell: two functions that must partition the same population, where one is written as a
  sibling of a nearby *aggregate* (a mode, a max, a first-non-null) that legitimately drops nulls.
  The `dropna()` gets inherited by proximity, and the two row sets diverge on exactly the rows
  nobody has a fixture for.
- Sub-rule for **derived vocabularies**: a set promoted from a validation-only role (loud config
  error) to an output-determining MASK (silently dropped rows) needs a **range-containment
  property** binding it to its producer. `CEDS_GRADE_CODES` is safe to mask on only because
  `grade_to_ceds`'s fallback literal happens to also be a table VALUE — an invisible coupling
  whose breakage is a green suite.
- The test that catches it is a **differential**: the same input with and without the one row the
  divergence hinges on. A single-sided assertion passes under both implementations.

Incident: plan 0043's blend-suppression gate. Suppressing a blend "when none of its grades receives
subject rostering" required a per-section grade map. Built as a sibling of `_build_grade_map` — the
natural place, three lines away — it inherits that map's `.dropna()`. But a blank grade converts to
`"UG"`, `"UG"` is not a homeroom grade, so that row SURVIVES the subject filter and is a real
student: the gate would suppress a blend that HAS a pupil, re-key them to a per-section class and
GROW `Classes.csv` — the precise opposite of the "strictly subtractive" property the design was
chosen for. **The plan's own full-suite simulation carried the bug**, so its measured "9 reds,
strictly subtractive" evidence attested to an implementation that must not ship; no shipped fixture
has a blank grade, so nothing was red. Caught by re-reading the null path, not by a test.
Second lesson from the same slice: a **simulated/monkeypatched flip measurement cannot see tests
that inspect module SOURCE or AST** (this repo has three), so those must be enumerated by hand
before a red count is quoted as complete.
Beneficiary roles: `implementer-architect`, `plan-reviewer`, `architect-reviewer`.

---

## verification — "A structural pin must inspect the module it names as the risk"  [staged 2026-08-14, plan 0043 Stage 7]

- This repo leans on **positive-count source pins** (`source.count("<spelling>") == 1`) to lock a
  DRY decision that no type or test can otherwise express. They work — but only over the modules
  they actually read, and that set is invisible from the assertion.
- 0043 shipped a pin whose docstring said *"it lives in the blended suite rather than the grades
  one **because this module is where that second spelling would be written**"* — and then inspected
  `grades` only. The one site the sentence named as THE risk passed green. The pin looked like a
  lock and was a decoration for the case it existed to catch.
- **The rule:** a source/AST pin must enumerate every module in its stated blast radius, and the
  enumeration belongs in a named constant (`MODULES = (...)`) so the gap is visible at the
  assertion rather than inferable only from prose. Report the per-module counts in the failure
  message; a bare `2 != 1` does not say WHERE.
- **Every such pin needs its own mutation check**, and it is cheap: add the forbidden spelling to
  the module in question, confirm red, revert. 0043's widened pin was verified this way. Note the
  first attempt at that mutation failed with `NameError` instead of the assertion, because the
  module did not import the constant yet — a mutation that dies on import proves nothing, so the
  probe must be made faithful (add the import too) before the red is believed.
- Generalisation: this is the *"no vacuous greens"* rule applied to the pins themselves. A test
  that inspects source is exempt from the usual signal that it is wired up — it never touches the
  behaviour, so it cannot fail for the ordinary reasons — which makes it the test class most
  likely to be silently scoped wrong.

Incident: found in the Stage-7 audit of 0043, in a pin the same plan had introduced two commits
earlier *specifically* to lock the row-set-identity invariant. Related, same slice: the plan's
prose asserted a fact about `ARCHITECTURE_TREE`'s contents that had never been true (the sentence
lived in `ROADMAP`), and it was restated five times — including in a disposition table marked
"APPLIED" — without anyone re-opening the file. Both are the same failure: **a claim about a
file's contents, made without reading that file.** The pin is the mechanised version of it.
Beneficiary roles: `implementer-architect`, `plan-reviewer`, `architect-reviewer`.
