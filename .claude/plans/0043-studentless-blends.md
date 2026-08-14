# 0043 — suppress studentless blended classes (ungate the timetable-scope rule)

- **Status:** **Spec'd — awaiting Stage 5 owner approval.** (2a draft → 2b six-lens panel → 2c → Stage 3 gate → per-row delta re-gate → all 15 required changes applied → Stage 4 spec.)
- **Roadmap item:** `docs/claugentic-ROADMAP.md` → *"2026-08-13 — NEXT (plan 0043): blended detection ignores `homeroom_grades`…"*
- **References:** `docs/claugentic-INVARIANTS.md:71` · `docs/developer/output-contract.md` · plan `0042-class-rostering-grades.md` (1a + 1b landed)
- **Owner decisions (2026-08-14):** land **unconditionally** · **fold in** the `create_name` guard. *(Both taken before the re-key impact below was known — see §Open question for the owner.)*

---

## Problem

A blended class whose sections' **mode** grades are all homeroom grades is emitted with a teacher and **zero students** — a duplicate of rostering that already happened correctly via the homeroom path.

- `BlendedClassDetector.detect` (`blended.py:57`) never applies the homeroom/subject split.
- The subject paths do (`classes.py:226`, `enrollments.py:186`), so no homeroom-grade student receives a subject enrollment.
- `_emit_missing_blended_classes` (`classes.py:255`) emits the class row *because* the subject path produced none, and `_blended_teacher_enrollments` (`enrollments.py:235`) emits its teacher row unfiltered.

### Verified, not assumed (2026-08-14)

| Claim | Evidence |
|---|---|
| It happens today | Golden: `BLENDED_7474018_T0001003_1_1_2_2_2026` ("Homeroom 2 / Homeroom 4 (02/04)") — 1 teacher, 0 students; the **only** studentless class of 156. |
| Golden reflects current code | 113 passed across `test_regression_sd74` + `test_blended_classes` + both scope suites. |
| Nothing is lost | Its two pupils are rostered via `7474018_DIV 2_2026` / `7474018_DIV 8_2026`. |
| **PRE-EXISTING**, not a 0042 artifact | Golden row dates to `506d63d` (2026-06-04). Only `sd83` sets a scope key, so `resolve_timetable_scope` → `None` for the other seven and 0042's `is not None` branch never evaluated. `blended.py` history shows no homeroom split ever applied to detection. |
| Will fire in real exports | Trigger = a teacher against two sections at one period whose mode grades are both in `homeroom_grades` (K–07) — an elementary split-grade class. The fixture case is literally two homeroom sections. |
| Closes a leak, not new policy | The golden's 113 subject classes carry grades `08–12` only. No homeroom-grade student ever receives a timetable class; the blended path is the one hole. |

### ⚠ SUPERSEDED by the owner's per-row decision (2026-08-14) — retained for the reasoning

**Everything in this subsection describes the MODE-gated design, which the owner replaced.** Under the per-row gate (see §Approach) the change is **strictly subtractive**: no re-key, no growth, no identity change. Measured — `TestShapeDefaultUnchanged` stays GREEN and the golden delta is unchanged at 156→155 / 340→339. Kept because it records *why* mode gating was rejected.

`_blend_grades` masks per-section **mode** grades (`blended.py:311-329`); the subject split masks **per-row** grades (`grades.py:237-240`). So a blend suppressed on mode can still carry out-of-scope minority pupils today, and their enrollment moves from `BLENDED_…` to `MT###_<year>` — with a **new** per-section `Classes.csv` row appearing.

Measured on the 0042 corpus (blend D = MTD1 mode 06 + one grade-10 pupil, MTD2 mode 05):

```
BEFORE: …, BLENDED_T010, BLENDED_T011, BLENDED_T012, BLENDED_T013, MTE_2026
AFTER : …, BLENDED_T010, BLENDED_T011, BLENDED_T012, MTD1_2026,    MTE_2026
```

Consequences the first draft missed:
- `Classes.csv` can **grow**, not only shrink.
- The impact is **class-identity re-assignment**, not merely row count — a different question for the partner than "fewer rows".
- Nothing in the pipeline observes it: the anomaly check is row-count-based (`pipeline.py:365-389`) and `--diff` prints only row/column deltas.
- SD74's golden is unaffected (its 4 survivors each carry a non-homeroom grade), so the golden stays a clean 2-line deletion — that is a property of **this fixture**, not of the change.

### Re-confirmation reasoning — corrected *(api-and-contracts)*

The first draft argued the trigger is "a closed list of seven and row count is on none of them." **Unsound**: `output-contract.md:35` says *"Any change to one of these **requires**…"* — sufficiency, not exhaustiveness — and item 6 names `DEFAULT_ACTIVE_VALUES`, which decides **which rows reach `Students.csv`**. So row membership *is* represented on the list.

**The sound argument, same conclusion:** the trigger's behaviour arm fires on constants behind an owner-**CONFIRMED** row. The only confirmed rows are `Students.EnrollStatus`, `Students.SchoolCode` and the attendance date format. **Every `Classes.csv` row is `pending owner confirmation`** (`output-contract.md:229-234`), so this invalidates nothing confirmed ⇒ no re-confirmation, MINOR bump. `e187ac8`'s rejection was about **orphans**; class + teacher row drop together, so that mode is unreachable.

Also corrected: the draft claimed this "brings the default path into line with what the doc already promises." It does not — `:250`'s promise is conditioned on a key being set. A **new** promise is being made where none existed.

## Open questions for the owner (Stage 5)

1. **Does a class arriving under a new Class ID affect student work attached to the old class in SpacesEDU?** — **CONDITIONALLY moot, not moot.** Under per-row gating *with row-set identity enforced* (see §Approach), a suppressed blend has zero enrollable rows, its sections produce no plain class either, `Classes.csv` only shrinks and no Class ID is re-assigned. **Without that enforcement the re-key returns** — the re-gate produced a live NaN-grade counter-example against the natural `dropna`-inheriting implementation, and the simulation that "measured" subtractiveness contained exactly that flaw. So this question is answered by an **implementation obligation**, not by the design choice alone: keep it open until slice 2's row-set-identity test is green, then close it. If the obligation ever proves impractical, the question is live again and rollout needs a second look.
2. ~~**The row-set versioning rule is an owner decision**~~ — **RULED by the owner, 2026-08-14.** `output-contract.md` had no versioning tier for a change in **which rows** an entity emits (`:17` covers columns/order/filename/encoding only), and as drafted this change would have authored the rule that graded it. The owner's ruling, to be written into `:17` as a general rule:

   > A change to **which rows** an entity emits is **MINOR** + a changelog row when it invalidates no owner-**CONFIRMED** row; it is **MAJOR**, and requires importer re-confirmation before merge, when it does.

   0043 therefore lands **MINOR**: every `Classes.csv` row is `pending owner confirmation` (`:229-234`), so nothing confirmed is invalidated. This is now a *rule the doc states*, not an inference the slice makes — which is the point, since re-deriving the reasoning from scratch is exactly how the closed-list error arose. Record as a `DECISIONS.md` line (the fifth).

**Pre-release measurement (do regardless):** `--dry-run --diff` writes nothing, skips the anomaly check and the store record, and prints exact per-entity deltas — run it against a real district export to replace the roadmap's unverified "411 blends" with a measured number. Under per-row gating the delta is now a pure count, so `--diff` alone is sufficient (no `Class ID` set-diff needed).

---

## Goals / Non-goals

**Goals**
- A blend none of whose grades receives subject rostering is suppressed from all three maps, for **every** district, scope key or not.
- The rule is spelled **once** and shared with the subject split it must agree with.
- Guard `create_name` against a missing Course Code column (owner-folded).
- Re-freeze the golden; keep every affected doc true.

**Non-goals** (each already tracked)
- A blend whose in-scope students are all **inactive** is still emitted studentless (residual #1). **This slice makes it executable** — see Test strategy §4.
- Mode-grade masking (residual #2) — the rule stays *necessary, not sufficient*.
- Straddling-blend fidelity (owner: education, 2026-08-13).
- `StudentAttendance` roster filtering.
- No new config key. No UI change.

---

## Approach

### Design decision (the one the roadmap flagged)

**Keep the resolver contract; derive the rostered set from the resolved scope.**

> **Rationale corrected (2c).** The draft claimed `None` must be preserved because "`resolve_student_scope` relies on it". That is **backwards** — `resolve_timetable_scope` *consumes* `resolve_student_scope`'s `None` (`grades.py:178-181`); nothing consumes the *timetable* resolver's `None` except the two branches this slice collapses.

The choice still wins, for two sound reasons:
1. `resolve_timetable_scope` reads **configuration**. Synthesising a 24-element CEDS complement is applying a default, not reading config — and would make `resolve_student_scope` the odd one out in its own module.
2. **Decisive:** the suppression log prints `sorted(timetable_scope)` (`blended.py:242-248`). Under the rejected alternative an unscoped district logs *"…not inside the timetable rostering scope ['01',…,'UG']"* — 24 codes, and untrue in spirit. Keeping `None` lets the message branch honestly.

**The preserved `None` must have a live reader**, or the next agent deletes it and is right to. So `_register_blends` keeps both values in hand and the log branches:
- `timetable_scope is None` → *"every grade in this blend is a homeroom grade"*
- otherwise → *"outside the configured rostering scope %s"*

### The single source of truth

```python
def timetable_rostered_grades(
    homeroom_grades: Sequence[str],
    *,
    timetable_scope: Optional[set[str]],
) -> set[str]:
    """The CEDS grades that EFFECTIVELY receive subject (timetable) rostering.

    Derived, never configured — there is no `timetable_rostered_grades` config
    key. Contrast `resolve_timetable_scope`, which reports what a district
    CONFIGURED (`None` = nothing).
    """
    if timetable_scope is not None:
        return set(timetable_scope)          # fresh, never the caller's object
    return set(CEDS_GRADE_CODES) - set(homeroom_grades)
```

Keyword-only with **no default** — both parameters are collections of CEDS codes and mypy cannot catch a swap (`global_config.get` returns `Any`); this matches `keep=` / `caller=` and CLAUDE.md's *"make the unsafe call unrepresentable rather than defaulted."*

`split_by_homeroom_grades(keep="subject")` masks on this set unconditionally, replacing its two-branch logic. **Equivalence is proven and measured** (`range(grade_to_ceds)` = `CEDS_MAPPING.values()` = `CEDS_GRADE_CODES`; junk homeroom entries are a no-op both ways; the full suite was run under the simulated change with zero unexplained failures).

> **The premise is incidental and must be pinned.** The proof holds only because `grade_to_ceds`'s fallback literal `"UG"` (`grades.py:103`) also happens to be a *value* in the table via the `"UGRADED"/"UNGRADED"/"UG"` rows. Delete those as a plausible tidy-up and `CEDS_GRADE_CODES` loses `"UG"` while `grade_to_ceds` keeps returning it — new code then **silently drops every unknown-grade row** with a green golden. See Test strategy §3.

Then the gate is unconditional, still keyed to the resolved scope (honouring `INVARIANTS.md:71`) and still **before the first `result.*` write**.

Behaviour by config: unscoped (7 configs) → `CEDS − homeroom`, **NEW suppression**; `"homeroom"` sentinel (SD83) → `set()`, unchanged; explicit list / inherited student bound → unchanged.

### The gate keys on PER-ROW student grades, not per-section mode *(owner decision, 2026-08-14)*

The roadmap's rule ("suppress when none of its **grades** is in the timetable scope") reads those grades from `_build_grade_map`'s per-section **mode** (`blended.py:382-394`), while enrollment is decided per-**row**. That mismatch is what produced the re-key. The owner's rule instead is: **suppress iff no student would actually enrol.**

- `detect()` builds a second map: MT ID → the set of **every** CEDS grade appearing in that section's schedule rows.
- `_register_blends` gates on `enrollable & rostered`, where `enrollable` is the union of those sets across the blend's sections.
- **`validate()` and `get_grade_range()` keep using the MODE map** — detection and naming are deliberately untouched, so no district's blend *qualification* or *display name* changes.

#### ⚠ The load-bearing premise is ROW-SET IDENTITY, not the mode-superset argument

The first framing of this section claimed safety from *"a mode grade in scope always implies rows in that grade, so per-row keeps a superset."* That is true but **not** what makes the design safe. The real requirement is that the gate's row set be **identical, by construction, to the set `split_by_homeroom_grades(keep="subject")` will filter** — same rows, same grade derivation. Any divergence re-creates the very re-key the per-row decision was taken to remove.

**A live counter-example proves it, and my own simulation carried the bug.** `_build_grade_map` opens with `.dropna()` (`blended.py:389`). Building the per-row map "alongside" it — which is exactly what the simulation did — inherits that `dropna`. But `grade_to_ceds(NaN)` returns **`"UG"`** (`grades.py:102-103`), and `"UG"` is not a homeroom grade, so a NaN-grade row **survives the subject filter and is a real student in the blend**.

> Blend of MT1 (rows `"03"`, `"03"`, NaN) + MT2 (`"04"`), default unscoped config: a `dropna`-built map yields `enrollable = {"03","04"}` → suppressed → the NaN row falls back to `MT1_2026` (`base.py:473-478`). **`Classes.csv` grows, a live student's Class ID is re-assigned, and a blend that had a student was suppressed.** Every property the per-row decision was meant to guarantee, broken.

**Therefore the per-row map MUST NOT `dropna` and MUST derive its grades through `grade_to_ceds` exactly as `split_by_homeroom_grades` does.** It is a sibling of the subject split, not of the mode map. No shipped fixture has a NaN grade, which is why the measurement did not surface it — that is a gap in the fixtures, not evidence of safety.

Consequences, **conditional on row-set identity holding**:

- **Strictly subtractive.** A suppressed blend has zero enrollable rows, so its sections produce no plain subject class either. `Classes.csv` only shrinks; no Class ID is re-assigned. *(FALSE without the fix above.)*
- **Residual #2 (mode masking) is NARROWED, not closed.** Corrected: `validate()` still qualifies on MODE (`blended.py:331-335`), so two sections with **colliding** modes never blend at all, and a minority in-scope pupil in one of them still lands in the plain per-section class — residual #2's sentence, reached by a different mechanism. What per-row gating closes is the *suppression* half; the *qualification* half remains. ROADMAP `:115` must be amended to say so rather than struck.
- **The Path-2 co-teacher residual is genuinely gone** — it was a consequence of re-keying.
- **The rule suppresses iff no in-scope schedule ROW exists.** Not "iff no student enrols": the modulus is the whole `active_student_ids` roster (`base.py:281-293`), not merely per-student activity. And intersecting with that roster is **not** a safe one-line extension — `filter_to_active` fails **safe** (empty roster ⇒ filter skipped), whereas a gate intersecting the roster would fail **closed** (empty roster ⇒ every blend suppressed). Out of scope, and to be recorded as such rather than as a trivial follow-up.

#### Known incoherence, accepted and recorded

Keeping `validate()`/`get_grade_range()` on MODE while gating per-row means a surviving blend can ship named `"… (05/06) 2026"` whose only student is grade 10 — and `Classes.csv`'s `Grade` cell is `""` for blends (`classes.py:359-360`), so the **name is the only grade signal**. Pre-existing on the default path (deliberately preserved), but **new on scoped configs**. Either accept and document, or fix the grade range — an owner call; fixing it would split slice 2.

**This changes 0042's landed semantics for a scoped district** (a scoped config now keeps a mode-masked blend it previously suppressed). No shipped config is affected — SD83 is the only one with a scope key and its scope is empty, so all its blends are suppressed either way — but it is a deliberate revision of landed behaviour and needs a `DECISIONS.md` line.

### Folded-in `create_name` guard *(design corrected in 2c)*

The realistic trigger is **not** a synthetic column-less frame: it is ClassInformation without `master timetable id` (SD40's real shape) → `_resolve_working_frame` falls back to the deduplicated schedule → the schedule names the column **`district course code`**. And `classes.py:306` already renames `district course code` → `course code` on the subject path.

So the guard resolves `COURSE_CODE`, then `DISTRICT_COURSE_CODE`, and only then falls back. **Fallback = skip the course segment** (matching the sibling guard three lines up at `blended.py:357-363`, which skips rather than substitutes), plus a `logger.warning` naming the missing column — every other degradation in this module logs one, and converting a loud `KeyError` into a silent `"Unknown Course"` trades one reliability defect for another.

Note the partner-visible edge: with no teacher column *and* no course code, `name_parts` = `["(01/02)", "2025"]`, so `len(name_parts) == 2` and the `"Blended Class …"` fallback at `:374` does **not** fire — the class ships named `"(01/02) 2025"`. Pin it.

---

## Architecture & holistic fit

- **Layering.** `grades.py` is the shared grade-vocabulary module; the derivation is vocabulary logic. **No new cycle edge** — the only new dependency is `CEDS_GRADE_CODES`, defined locally at `grades.py:97`.
- **`CEDS_GRADE_CODES` is promoted** from a config-boundary validation vocabulary to a runtime output-determining set. Same value, two failure modes now (loud config error vs. silently dropped rows). This binds it to `grade_to_ceds`: the "neutral grade-vocabulary module" escape hatch floated at `grades.py:45-46` must move **both**.
- **SOLID/SRP.** One function, one question. The module header (`grades.py:10-21`) must be re-indexed — do **not** split the file; splitting fragments the non-idempotency safety narrative at `:23-31`.
- **DRY.** The claim needs a **lock, not a comment** — this repo has already been bitten by exactly this shape (`INVARIANTS.md:71`). Precedent: `test_blended_classes.py:462-466` asserts `source.count("grade_to_ceds(") == 1`. Add the analogue.
- **Contract legibility.** `resolve_timetable_scope`'s docstring opens near-verbatim identical to the new function's; calling the wrong one silently restores the gated behaviour. De-collide both in the same commit.
- **Granularity caveat.** The helper unifies the grade *vocabulary*, not the *granularity* (per-row split vs per-section mode). One docstring sentence, to prevent over-trust.
- **Row-set identity is an architectural invariant, not an implementation detail.** The gate and `split_by_homeroom_grades` must partition the *same rows* by the *same derivation*; the helper unifies the grade **vocabulary**, which is necessary but not sufficient. This is what earns a new `INVARIANTS.md` entry (see Harness impact) — the `dropna` counter-example shows the natural implementation violates it.
- **Dimensions in scope:** `api-and-contracts`, `data-and-persistence`, `reliability-resilience`, `testing`, `maintainability-structure`, `observability-ops`, **`performance-efficiency`** *(re-gate #5 — a new per-section aggregation over the pipeline's largest frame; the draft's "Out: performance" was false)*. **Out:** `product-ux`, `security`.

---

## Decomposition (slices)

Split in 2c to answer the attribution objection: the draft bundled a rewrite of the mask producing 113 of the 155 surviving golden rows into the same commit as the re-freeze, while splitting a 3-line guard out for attribution. Inverted priorities.

- [ ] **Slice 1 — groundwork: NO CHANGE TO ANY CURRENTLY-SUCCEEDING OUTPUT.** *(Retitled per Stage-3 #1 — "zero behaviour change" was false.)* `timetable_rostered_grades` + both consumers + docstring de-collision + module header re-index + range-containment property + structural DRY pin + the `create_name` guard.
  **The guard IS a behaviour change** — it converts a run-killing `KeyError` (`blended.py:365`; `run_pipeline` re-raises → exit 1) into a warned degradation, and the `district course code` alias adds a naming path that never existed. No *currently-succeeding* run changes a byte, so the acceptance criterion stands, but its only proof is reasoning — the golden cannot witness a crash it never had.
  **Acceptance: the golden is BYTE-IDENTICAL and the full suite is green with no flips.**
- [ ] **Slice 2 — the suppression.** Gate unconditional + per-row map (row-set identity) + branching log + golden re-freeze + every test flip/rewrite + all doc updates + release note.
  **Acceptance: the golden diff is EXACTLY the two lines in §4, nothing else, AND the row-set-identity test is green.**

---

## Affected files

**Slice 1** — `src/etl/transformers/grades.py` (new function, subject-branch consumer, header + `resolve_timetable_scope` docstring; `why` comment at `:103`) · `src/etl/transformers/blended.py` (`create_name` guard + alias resolution + **one** warning) · `tests/test_class_rostering_grades.py` (helper unit rows beside `TestResolveTimetableScope`) · `tests/test_property_based.py` (range containment) · `tests/test_blended_classes.py` (structural DRY pin; `create_name` rows) · **`docs/claugentic-ROADMAP.md`** (strike the `create_name` `KeyError` entry) · **`docs/claugentic-DECISIONS.md`** (skip-not-substitute + the alias + why it declines the roadmap's recorded fix shape) · **`docs/claugentic-ARCHITECTURE_TREE.md:27`** (`grades.py`'s description gains the derived set).

> *Stage-3 #2:* without the ROADMAP/DECISIONS pair, slice 1 would close a tracked bug while the roadmap still claimed it open — and it **declines** that entry's recorded fix (`"Unknown Course"`) in favour of skipping the segment, which is a re-litigated decision and must be recorded, not silently substituted.

**Slice 2** — `src/etl/transformers/blended.py` (gate + per-row map + log) · `tests/snapshots/output/{Classes,Enrollments}.csv` · `tests/test_blended_classes.py` · `tests/test_class_rostering_grades.py` · **`tests/test_transform_classes.py`** *(missed in the draft)* · `tests/test_transform_enrollments.py` (de-vacuum) · `tests/snapshots/generate_synthetic.py` (regen docstring) · `docs/developer/output-contract.md` · **`docs/partner/how-classes-work.md`** · **`docs/partner/faq.md`** · `docs/developer/adding-district.md` · `docs/developer/architecture.md:122` · `docs/index.md:73` · **`docs/claugentic-ARCHITECTURE_TREE.md:182`** · `docs/claugentic-{INVARIANTS,ROADMAP,DECISIONS}.md` · `CLAUDE.md` · release note.

**`ARCHITECTURE_TREE` DOES change** *(Stage-3 #4 — the draft conflated the hook's trigger with the tree's accuracy)*. No file is added/moved/removed, so the **gate** does not fire, but two descriptions go stale and must be corrected by hand: `:27` describes `grades.py` as "`resolve_timetable_scope` … `None` = none in force" with no mention of the derived set, and `:182` says `TestShapeDefaultUnchanged` "is a gate-CLOSED pin plan 0043 deliberately flips" — under per-row gating that class **stays green**, so `:182` needs **re-wording, not deletion**. (Still no new `tests/test_grades.py`.)

---

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| **⚠ THE PRIMARY RISK — a per-row map that is not row-set-identical to the subject split re-creates the re-key.** Verified live: `grade_to_ceds(NaN)` = `"UG"`, `"UG"` ∉ `homeroom_grades`, so a NaN-grade row survives the subject filter and is a real student — but a map built alongside `_build_grade_map`'s `.dropna()` (`blended.py:389`) omits it, suppressing a blend that had a student, growing `Classes.csv` and re-assigning a live Class ID. **The 2b simulation contained this exact flaw**, so the "strictly subtractive, measured" evidence attests to an implementation that must not ship. | Build the per-row map as a sibling of `split_by_homeroom_grades`, **not** of the mode map: no `dropna`, grades derived through `grade_to_ceds`. Pin with the row-set-identity test (§3) — the acceptance criterion for slice 2. Add an `INVARIANTS.md` entry so the next reader cannot re-derive it wrongly. |
| **Partner-visible: rows removed** (strictly subtractive **once the above holds**) | No confirmed row invalidated ⇒ MINOR per the owner's ruling. `--dry-run --diff` pre-release for a measured per-district number. |
| **Performance — back IN scope** *(re-gate #5; the draft's "Out: performance" was false)*: a new per-section aggregation over the pipeline's largest frame (SD40 = hundreds of thousands of schedule rows). | ONE `groupby` pass built beside the existing `_build_grade_map` pass over the same frame — never a per-blend rescan, never a row-wise `apply` inside the session loop. Vectorised `.isin` in `split_by_homeroom_grades` is preserved. |
| **Desktop Convert BLOCKS on the anomaly** — `convert.py:297-307` returns `NEEDS_ANOMALY_ACK` and writes **nothing** until acknowledged. The draft's "warning, not a failure" is true only for `run_pipeline`. | Release note must name all three surfaces: Convert ack gate, Home WARNING verdict (`home_status.py:488`), Run History reason (`run_history.py:274`). Exit-code contract genuinely unchanged. |
| **`_emit_missing_blended_classes` loses ALL coverage** — measured 4 covering tests → **0**, while the branch stays live (its only remaining trigger is non-goal #1). | Do **not** flip `test_transform_classes.py:88` to an absence assertion — **rewrite its scenario**: a straddling blend whose in-scope students are all inactive. Keeps the `e187ac8` orphan guard, keeps the emitter covered, makes residual #1 executable. |
| **Golden regeneration is a documented trap** — `generate_synthetic.py:10-14` regenerates via the **live** config, but the test runs the **frozen** config, which pins dates (`tests/snapshots/config/sd74myedbc_mapping.yaml:47-50`). The documented command makes the golden time-dependent and breaks `test_classes_have_fixed_dates`. | Regenerate through the same `bundle_mappings_dir`/`user_mappings_dir` patches the fixture uses; fix the docstring in the same commit. |
| **Silent data loss if `"UG"` rows are ever tidied away** | Range-containment property test + a `why` comment at `grades.py:103`. |
| **ROADMAP:119's proposed remedy becomes wrong** — it suggests gating the empty-both-entities floor on `class_rostering_grades` being set, which after 0043 is silently dead for exactly the `INVARIANTS.md:71` reason. | Amend that roadmap item in this slice. |
| ~~**A re-keyed section can lose its ClassInformation-only co-teacher**~~ — Path 2 (`enrollments.py:334`) has no `generate_class_id` fallback. | **RESOLVED, not deferred.** Re-gate confirmed this was purely a consequence of re-keying; with row-set identity enforced there is no re-key, so the residual is gone. Row kept struck-through so it is not re-discovered as new. |
| **Naming incoherence on scoped configs** — `validate()`/`get_grade_range()` stay on MODE while the gate is per-row, so a surviving blend can be named `"… (05/06)"` with only a grade-10 pupil; `Classes.csv`'s `Grade` is `""` for blends (`classes.py:359-360`), making the name the only grade signal. | **RULED 2026-08-14: accept + document.** Naming stays on mode grades, so blend NAMES stay byte-identical for every district and no second partner-visible change rides this one. Unreachable on any shipped config today (SD83's scope is empty). Document in `output-contract.md`'s blended-name note + `adding-district.md`; record as a `DECISIONS` line and a ROADMAP entry so it is revisitable. |
| Deliberate flips mistaken for regressions | Every flip enumerated below with its new value — **plus** the likely 10th (§1). |
| Resolver-`None` pins reddening | Impossible under this design; if red, the implementation drifted to the rejected alternative. |

---

## Test strategy

### 1. Must FLIP — the complete MEASURED set for the PER-ROW gate (9 reds)

Measured by simulating the exact end state (unconditional gate + per-row basis) as a load-time pytest plugin over the full suite: **9 failed, 4053 passed, 32 skipped** (`test_cli_entry.py` excluded — pre-existing `tomllib` gap on local Python 3.10, unrelated). Superseding the 7-red mode-gated set, which is **no longer valid**.

| Test | New expectation |
|---|---|
| `test_blended_classes.py:362 test_flag_OFF_…STILL_registered` | suppressed from all three maps; **rename** + rewrite the "0043 deletes the golden" docstring (after the flip, unit and golden *agree*). |
| **`test_transform_classes.py:88 test_blended_classes_in_output`** | **scenario rewrite, NOT an absence flip** — it is the `_emit_missing_blended_classes` / `e187ac8` orphan guard (see Risks). |
| `test_regression_sd74.py` — `test_output_row_counts_match_golden`, `test_classes_matches_golden`, `test_enrollments_matches_golden` | the re-freeze. **Confirmed identical to the mode-gated outcome: Classes 156→155, Enrollments 340→339.** |
| `TestShape2NoHomeroomsSeniorTimetableOnly::test_no_homeroom_classes_and_only_senior_subject_classes` | blend D now survives on its grade-10 pupil, so `MTD1_2026` is no longer a plain subject class. |
| `TestShape3SplitHomeroomAndTimetable::test_subject_classes_only_for_ten_to_twelve` | same cause. |
| `TestShape3SplitHomeroomAndTimetable::test_the_wholly_unrostered_blends_are_suppressed` | same cause. |
| `TestShape3SplitHomeroomAndTimetable::test_mode_masked_section_falls_back_to_its_plain_class_with_no_orphan` | **obsoleted by design** — per-row gating removes the fallback this pins. Rewrite as the positive statement (the mode-masked blend SURVIVES and carries its in-scope pupil), which is residual #2 closing. |

**`TestShapeDefaultUnchanged` STAYS GREEN under per-row gating** — both tests, measured. The default path changes *only* by removing genuinely studentless blends, so the class name remains true and needs **no rename**. (Under mode gating both its tests flipped and it needed renaming; that requirement is withdrawn.) The four flips are concentrated in the 0042 **scoped-config** suites, which have no shipped consumer.

**⚠ The measured set has a known blind spot — expect a 10th red** *(re-gate #4)*. `test_blended_classes.py:462-466` asserts `inspect.getsource(...).count("grade_to_ceds(") == 1`. A load-time plugin cannot perturb module **source**, so that test passed **by construction** in the simulation and its result carries no information. The real implementation adds a `grade_to_ceds` call for the per-row map and will either turn it red or satisfy it by routing through a helper. **Neither outcome may be resolved by relaxing the assertion** — it is the DRY lock this plan leans on. Decide the shape at spec time; treat a green here as unproven until re-measured against real code.

**Method note, recorded so it is not over-trusted again:** simulation is authoritative for *data-path* behaviour and worthless for *source-inspection* tests — and it inherits any flaw in the code it simulates (it carried the `dropna` bug). The measured 9 are a floor, not a ceiling.

### 2. Must STAY GREEN — the proof set
`TestSD74StudentScopeDifferential` (both blends) · all three resolver-`None` pins · **`test_contract.py:803 test_every_enrollment_class_exists_in_classes`** (the real orphan oracle across all 12 configs — the draft cited `test_contract.py` only for columns; its fixtures produce **no blends**, so it is otherwise blind here) · `test_zero_orphan_enrollments.py` · `test_class_rostering_grades.py:470` Path-2 no-scope twin (green only because blend A straddles 07/08 — record the reason so it is not deleted when its name stops meaning anything).

### 3. New coverage
- `timetable_rostered_grades`: `None` → complement. *(The draft's other three rows and its "equivalence property" were cut — tautologies over set difference. The real risk is `grade_to_ceds`'s **range**, which that test never touched.)*
- **Range containment** (`test_property_based.py`, beside the existing grade properties): `@given(st.text())` → `grade_to_ceds(x) in CEDS_GRADE_CODES`, plus `None`/`NaN`/`pd.NA`/`""`.
- **Structural DRY pin** *(Stage-3 #9b — an absence sweep is a textbook vacuous green under this repo's own rule)*: use the house **positive-count** form the plan cites as precedent (`test_blended_classes.py:462-466`, `source.count(...) == 1`) — assert the complement is spelled **exactly once**, in `grades.py`.
- **⚠ ROW-SET IDENTITY — the acceptance test for slice 2.** A blend whose sections carry a **NaN / blank grade row**: that row must count as enrollable (`grade_to_ceds` → `"UG"` ∉ homeroom), so the blend **survives**, no per-section fallback class appears and no Class ID is re-assigned. Verified today: a `dropna`-built map yields `{'03','04'}` where the correct map yields `{'03','04','UG'}`. *(This replaces the draft's "re-key case", which pinned the behaviour the per-row decision exists to remove.)* Pair it with the general property: for any section, the gate's grade set **equals** the set `split_by_homeroom_grades` derives from the same rows.
- **Positive twins** for every absence assertion — a detector that simply stopped detecting must fail. Co-locate in `TestBlendSuppressionByRosteringScope` with only homeroom membership differing (`_HOMEROOM_KG_TO_07` minus `"03"`). Add `assert not enrollments.empty` (house pattern, `test_class_rostering_grades.py:342`). *(`TestDetectBlendedClasses::test_detects_blended_from_class_info` plays this role only by accident today — it omits `homeroom_grades`; don't rely on it.)*
- **Both log branches pinned** *(Stage-3 #8)*: a `caplog` row each for `timetable_scope is None` ("every grade in this blend is a homeroom grade") and the configured-scope branch. Without these the preserved `None` ends slice 2 with **no tested reader** and the next agent deletes the branch on a green suite.
- **De-vacuum `test_transform_enrollments.py:158`** — `:172`'s `if not result.empty and …blended_teacher_map:` can pass with zero assertions executed. *Corrected (Stage-3 #7): this does **not** happen under this change* — the test sets `homeroom_grades = []`, so nothing is suppressed and the assertion runs. Worth fixing as hygiene; **not** attributable to this slice.
- `create_name`: alias present → real titles; genuinely absent → skip + WARNING; no-teacher-and-no-course-code → exact name `"(01/02) 2025"`.

### 4. The golden oracle
No differential is constructible (the rule is unconditional by design), so the **reviewed diff is the oracle**. Acceptance = exactly these two removed lines, nothing else:

```
Classes.csv     157 → 156 lines
  -BLENDED_7474018_T0001003_1_1_2_2_2026,Homeroom 2 / Homeroom 4 (02/04) 2026,,7474018,2025-08-25,2026-07-25
Enrollments.csv 341 → 340 lines
  -BLENDED_7474018_T0001003_1_1_2_2_2026,T0001003,teacher,7474018
```
Students/Staff/Family: **0 changed**. Slice 1's acceptance is the same diff, **empty**.

### 5. Doc gates
- Add a `test_output_contract_doc.py` row asserting front-matter `contract_version` == the newest changelog row's first cell — currently `contract_version`/`emitted_by`/changelog have **zero** test references (the repo's own no-vacuous-greens rule, applied to the contract header).

**Gates:** full pytest (80%), snapshot, `make validate-config`, ruff check + format, mypy, bandit, tree-check. **Land gate:** read and quote CI's three-OS result.

---

## Doc corrections required (2c — the draft listed one file; it is six)

- `output-contract.md` — `:246` is a **correction, not an addition** (it currently states blends "merge into one" unconditionally). `:248`/`:250` frame suppression as **opt-in**, so editing `:246` alone leaves the section self-contradictory — reduce them to what they still add (a *narrower* set). Bump `emitted_by`. **Write the owner's row-set versioning rule into `:17`** (open question #2, ruled): *a change to which rows an entity emits is MINOR + a changelog row when it invalidates no owner-CONFIRMED row; MAJOR, requiring importer re-confirmation, when it does.* Then add the changelog row — **authored AFTER the row-set-identity test is green, not before** *(Stage-3 #6)*: only then is "rows removed" accurate. If that test is not green the change is not subtractive and the row must say so instead. The 1.0.0 row's *"No emitted bytes changed by this document"* makes the inverse statement necessary either way.
- **Word the new guarantee as a GRADE-SCOPE rule and name the residual in the same breath.** Placed beside *"a homeroom with no active students is not emitted"* on a **GUARANTEED** column block, an unqualified sentence reads as "we never ship a studentless class" — which non-goal #1 makes false.
- **`docs/partner/how-classes-work.md:32,40,65,76` and `docs/partner/faq.md:49`** — both use the suppressed case as their worked example (*"a combined **Grade 1/2** class"*, *"(03/04)"*; base `homeroom_grades` is KG–07, so both vanish). Replace with a surviving blend (e.g. 07/08) and add the suppression step to the 5-step walkthrough.
- `docs/developer/adding-district.md:156,209` — frames blend-drop as key-conditional; hoist to a general rule.
- `architecture.md:122`, `docs/index.md:73` — one-line qualifiers.
- `INVARIANTS.md:71` — **minimal** edit: the gate is now unconditional and the `None` discipline moves *inside* `timetable_rostered_grades`; the rule (never branch on key presence) still binds.
- Release note — named as a deliverable, so "notify the partner" is discharged by the slice rather than by memory.

---

## Stage 2b advisory panel (complete — advisory, contributed not gated)

Six lenses: `api-and-contracts`, `reliability-resilience`, `testing`, `maintainability-structure`, `data-and-persistence`, `yagni-sentinel`. All returned GAPS/over-built; none blocked the mechanism. Every lens independently confirmed the suppression is orphan-free across all seven consumers of the three maps, including the co-teacher Path 2 the draft never mentioned (safe because it has no `generate_class_id` fallback).

**Conflict adjudicated by evidence:** `maintainability-structure` claimed `_subject_ids` stays `{"MTE_2026"}`; `reliability-resilience`, `data-and-persistence` and `testing` said it gains `MTD1_2026`. The latter is correct — verified directly against the fixture (`test_class_rostering_grades.py:143-152`, MTD1 = three grade-06 pupils + one grade-10) and by `testing`'s full-suite simulation. `yagni-sentinel` likewise predicted that assertion would hold; it does not.

**yagni-sentinel accepted:** the tautological equivalence test and three padded unit rows cut; the speculative "third scope key" future-proofing bullet deleted; `INVARIANTS.md` edit kept minimal; `tests/test_grades.py` not created. **Partially accepted:** its CUT 1 (drop the `split_by_homeroom_grades` consolidation) is **declined on substance** — `testing` measured the refactor behaviour-preserving and `maintainability` showed the DRY lock is what `INVARIANTS.md:71` exists to demand — but its *attribution* argument is **fully honoured** by the two-slice split, which is a stronger remedy than the commit split it proposed.

---

## Review  _(Stage 3 — plan-gate)_

**Verdict: CHANGES REQUIRED** *(2026-08-14, plan-gate)*

The mechanism is sound, the path (full pipeline) is right, and the *Architecture & holistic fit* section is genuinely reasoned rather than hand-waved (layering, the `models`↔`grades` cycle, the promoted-vocabulary failure-mode shift, the DRY *lock* with a real precedent, the granularity caveat). I re-derived the headline facts rather than taking the plan's word:

- **Golden claims hold exactly.** `Classes.csv` = 157 lines / 156 rows, `Enrollments.csv` = 341 / 340; `BLENDED_7474018_T0001003_1_1_2_2_2026` is the **only** class with zero student rows (156 checked), and there are **zero** orphan Class IDs today.
- **The mask equivalence holds.** `grade_to_ceds` (`grades.py:100-103`) returns only `CEDS_MAPPING.get(...)` or the literal `"UG"`, and `"UG"` is a table *value* — so `range(grade_to_ceds) ⊆ CEDS_GRADE_CODES` and `~isin(homeroom)` ≡ `isin(CEDS − homeroom)` for every value the column can hold, including junk entries in `homeroom_grades`. The "incidental premise" warning is correct and the property test is the right lock.
- **The re-key adjudication is correct.** `test_class_rostering_grades.py:143-152` gives MTD1 three grade-06 pupils + one grade-10; with blend D suppressed that pupil re-keys to `MTD1_2026` and a per-section Classes row appears. `maintainability-structure`'s dissent was wrong; the plan's ruling stands.
- **The Path-2 residual is real.** `enrollments.py:330-343` maps MT ID → blended id with no `generate_class_id` fallback, so a ClassInformation-only co-teacher of a re-keyed section loses their row.

The changes below are about **claims, traceability and one untested reader** — not the design.

### Required changes

1. **Slice 1's "ZERO behaviour change" label is false; the acceptance criterion is fine.** The `create_name` guard converts a run-killing `KeyError` (`blended.py:365`, unguarded `session_group[COURSE_CODE]`; `run_pipeline`'s outer handler re-raises → exit 1) into a warned degradation, and the `district course code` alias adds a naming path that has never existed. No *currently-succeeding* run changes a byte — so the golden and the suite are safe — but "zero behaviour change" is not what ships. Retitle to **"no change to any currently-succeeding output"**, and state in one line that the guard is a *fix* (crash → degraded success) whose only proof is reasoning, not the golden. Also state explicitly that `split_by_homeroom_grades`' `keep="homeroom"` + `timetable_scope` **RAISE** (`grades.py:227-234`) survives the unification — the helper makes it look redundant and it is not.
2. **Slice 1 must carry its own ROADMAP + DECISIONS discharge.** It closes the tracked entry *"2026-08-13 — `blended.create_name` raises `KeyError: 'course code'`"* — yet slice 1's affected-files list (`:159`) contains no doc at all, so the roadmap would claim an open bug through slice 2. Worse, that entry's recorded fix shape is *"guard the column and fall back to the existing default"* (i.e. `"Unknown Course"`); the plan **declines it** in favour of skipping the segment (`:128`). That is a re-litigated decision and needs a `DECISIONS.md` line (skip-not-substitute + the `district course code` alias + why), plus the ROADMAP strike, **in slice 1**.
3. **Log the missing-column warning ONCE, not per blend.** The guard sits inside `create_name`, which `_register_blends` calls once per surviving blend (`blended.py:260`). The roadmap's own SD40 figure is **411 blends** — that is 411 identical WARNINGs. The sibling degradations the plan cites (`_build_grade_map:393`, `_build_course_title_map:401`) fire once per `detect`. Resolve the column once (or count-and-summarise like `suppressed`) and log once.
4. **Drop the "no `ARCHITECTURE_TREE` change" claim; add two tree lines to slice 2.** `:163` conflates the *gate's* trigger (adds/moves/removes) with the tree's *accuracy*. Both descriptions go stale: `docs/claugentic-ARCHITECTURE_TREE.md:27` describes `grades.py` as "`resolve_timetable_scope` … `None` = none in force" with no mention of the new derived set, and `:182` says `TestShapeDefaultUnchanged` "is a gate-CLOSED pin plan 0043 deliberately flips" — which is exactly what slice 2 flips.
5. **The row-set versioning rule is an OWNER decision, not an implementer edit.** `:227` asks slice 2 to "close the row-set versioning silence" in `output-contract.md` — i.e. the change that first exercises the gap would also author the rule that grades it MINOR. Move that to the Stage-5 open-question block beside the existing SpacesEDU question, and land whatever the owner rules. (The MINOR reasoning itself is sound: the trigger list at `output-contract.md:37-44` is a closed list of source constants and none of them is the blend rule, and every `Classes.csv` row is `pending owner confirmation`. Note this **contradicts** the ROADMAP 0043 entry's *"output-contract.md classes this as requiring importer re-confirmation"* — record the override as a DECISIONS line, don't leave two documents disagreeing.)
6. **Fix the changelog instruction — it contradicts the plan's own finding.** `:227` says to add a row "saying rows were removed". The plan established at `:41-42` that `Classes.csv` can **grow** and that the real change is **class-identity re-assignment**. The changelog row and the release note must say *removed **and/or** added, with some sections re-keyed to a new Class ID* — the partner-visible fact is the re-key, not the count.
7. **Correct the `test_transform_enrollments.py:158` claim (verified wrong).** `:204` asserts that test "leaves it green with zero assertions **under this very change**". It sets `global_config_copy["homeroom_grades"] = []` (`test_transform_enrollments.py:163`), so the effective rostered set is `CEDS − ∅` = every code, the 01/02/03 blend survives, `blended_teacher_map` is non-empty and the assertion **does** run. De-vacuuming it is still worth doing as hygiene — but say so honestly instead of attributing it to this change. (This is the third over-claim found in this plan; the remaining measured claims all verified, so the pattern is wording, not method.)
8. **Pin BOTH log branches, or the preserved `None` has no tested reader.** The design at `:91-93` keeps `None` alive *because* the suppression log branches on it — after slice 2 that log message is its **only** consumer in `blended.py`, and the test strategy asserts no log text anywhere. Add a `caplog` row per branch ("every grade in this blend is a homeroom grade" vs "outside the configured rostering scope %s"). Otherwise the justification for keeping `None` is itself unverified and the next agent deletes the branch with a green suite.
9. **Two smaller consistency fixes.** (a) The Path-2 co-teacher loss is promised to Non-goals by the Risks row at `:177`, but the Non-goals list at `:70-76` does not contain it — add it, since that section claims "each already tracked". (b) The structural DRY pin (`:201`) is phrased as an absence sweep ("no module outside `grades.py` contains a second spelling") — a textbook vacuous green under this repo's own rule. Use the house **positive-count** form the plan itself cites as precedent (`test_blended_classes.py:462-466`, `source.count(...) == 1`): assert the complement is spelled **exactly once**, in `grades.py`.

### Sizing / completeness check

- **Slice 1 — OK, no split.** Session-sized (one new function, one branch collapse, one guard, four test files) and it lands vertically complete *once #1/#2 are folded in* — without the ROADMAP/DECISIONS discharge it leaves a stale tracked bug, which is debt. **The intermediate state is acceptable:** slice 1 leaves the studentless-blend bug live, but that bug is **pre-existing since `506d63d` (2026-06-04)** and nothing slice 1 builds is half-built — the new helper has a live consumer (`split_by_homeroom_grades`) from the moment it exists, so there is no dead code and no `TODO`. "No half-done state" governs the slice's own work, not the age of the defect it precedes. One caveat worth a comment in the code: between the slices, `timetable_rostered_grades` documents itself as *the* answer to "which grades receive subject rostering" while `blended.py` still answers it differently — say so in one docstring line so the interim gap reads as deliberate.
- **Slice 2 — OK, no split, but it is at the top of the range** (~20 files). Do **not** split the docs out: the eight doc files and the release note describe behaviour this slice changes, so landing them separately would leave `docs/partner/how-classes-work.md` and `faq.md` teaching a worked example (`Grade 1/2`, `(03/04)`) the code no longer produces — a documentation lie, which is the half-done state the rule forbids. Recommend a fixed intra-slice order — code + log branch → golden re-freeze (through the patched `bundle_mappings_dir`/`user_mappings_dir` fixture, per the trap at `:174`) → tests → docs → release note — because the doc tail is the part most likely to be skimped under context pressure, and it is the partner-facing half.

### Harness impact

- **`docs/claugentic-INVARIANTS.md:71` — the minimal edit must add a distinction, not just soften wording.** After 0043 the resolver's `None` **no longer means "nothing is suppressed"**; it means "no scope was CONFIGURED", and the effective rostered set is derived. Both facts now live in `grades.timetable_rostered_grades`. If the edit only relaxes "gated", the next reader re-derives `CEDS − homeroom` at a call site and re-opens exactly the class of bug this invariant exists to prevent.
- **`docs/claugentic-DECISIONS.md` — three lines, not one:** (i) 0043 lands **without** importer re-confirmation, with the `output-contract.md:37-44` closed-list argument (this overrides the ROADMAP entry's opposite claim); (ii) `create_name` **skips** the course segment rather than substituting `"Unknown Course"`, deviating from the recorded roadmap fix; (iii) whatever the owner rules on the row-set versioning rule (#5).
- **`docs/claugentic-standards/CANDIDATES.md` — one genuinely universal lesson:** *a derived vocabulary set promoted from a validation-only role to an output-determining one needs a range-containment property binding it to its producer.* `CEDS_GRADE_CODES` is safe to mask on **only** because `grade_to_ceds`'s fallback literal happens to also be a table value — an invisible coupling whose breakage is a green suite and silently dropped rows. That is the same family as the repo's *no vacuous greens* rule and belongs upstream, not just in this plan's risk table.
- **No new STANDARD and no new agent** — this is ETL work inside patterns the repo already owns. `CLAUDE.md:165`'s "stays GATED … until plan 0043 ungates it" is already on slice 2's list; keep it there.

### Disposition of the 9 required changes (2026-08-14)

> **Corrected 2026-08-14 after the re-gate's finding 0.** The first version of this table marked 7 of 9 "Applied" while the plan body still carried the original text — **intentions recorded as completions**. Statuses are now verified against the body: "AGREED" means decided-not-written; "APPLIED" means the body carries it. **All 9 are now APPLIED** (second pass, 2026-08-14) — each row names where.

| # | Status | Where in the body |
|---|---|---|
| 1 | **APPLIED** | §Decomposition — slice 1 retitled *"no change to any currently-succeeding output"*, with the guard named as a real behaviour change (crash → degraded success) whose only proof is reasoning. |
| 2 | **APPLIED** | §Affected files — slice 1 now carries `ROADMAP` + `DECISIONS` + the blockquote recording that it **declines** the roadmap's `"Unknown Course"` fix shape. |
| 3 | **APPLIED** | §Affected files — "**one** warning"; column resolved once per `detect`, never 411. |
| 4 | **APPLIED** | §Affected files — "**`ARCHITECTURE_TREE` DOES change**", both stale lines named, `:182` re-worded not deleted. |
| 5 | **APPLIED** | Stage-5 open question #2, **ruled**; the rule itself is now in §Doc corrections for `:17`. |
| 6 | **APPLIED** | §Doc corrections — changelog row authored **after** the row-set-identity test is green, and says so. |
| 7 | **APPLIED** | §Test strategy §3 — claim corrected in place; de-vacuuming kept as hygiene, not attributed to this slice. |
| 8 | **APPLIED** | §Test strategy §3 — a `caplog` row per log branch. |
| 9 | **APPLIED** | (a) §Risks — Path-2 row struck as RESOLVED. (b) §Test strategy §3 — DRY pin uses the positive-count form. |

### Re-gate findings 1–6 (per-row delta) — disposition

| # | Status | Disposition |
|---|---|---|
| 1 | **APPLIED** | "Strictly subtractive in ANY config" was **FALSE**. Root cause + the `dropna` counter-example + the row-set-identity requirement are now in §Approach. **The simulation shared the bug**, so the measured result attested to an implementation that must not ship. |
| 2 | **APPLIED** | Residual #2 is **narrowed, not closed** — `validate()` still qualifies on MODE. ROADMAP `:115` amended, not struck. |
| 3 | **APPLIED** | The MODE-name / per-row-student incoherence is recorded as accepted-and-documented, with the owner call named. |
| 4 | **APPLIED** | §Test strategy §1 — the blind spot, the probable 10th red, the rule that it may **not** be resolved by relaxing the assertion, and a standing method note that simulation is worthless for source-inspection tests and inherits the flaws of what it simulates. |
| 5 | **APPLIED** | §Architecture (`performance-efficiency` moved **in** scope) + §Risks (the cheap shape specified: one `groupby` beside the existing pass, no per-blend rescan, no row-wise `apply` in the session loop). |
| 6 | **APPLIED** | The "necessary and sufficient modulo student activity" claim was imprecise twice; corrected in §Approach, incl. that the "one-line extension" would fail **closed** where `filter_to_active` fails **safe**. |

**Sizing verdict accepted:** both slices pass, slice 2 not split, intra-slice order code → golden → tests → docs → release note. **Harness impact accepted in full**, with one addition: the `DECISIONS.md` set grows to **four** lines — the fourth records that per-row gating deliberately revises 0042's landed suppression semantics for scoped districts (no shipped consumer).

**Re-gate required?** The mechanism changed after the gate ran. The gate's findings were about claims, traceability and one untested reader — all still apply — but the per-row basis is a design change it never saw. Re-gate the delta before Stage 4.

### Re-gate (per-row delta) — 2026-08-14

**Verdict: CHANGES REQUIRED**

The per-row basis is the **right direction** and I could not break its core mechanism: suppression still precedes the first `result.*` write, `assign_class_ids` (`base.py:473-478`) is only ever reached by rows the subject split already kept, and Path 2 (`enrollments.py:330-343`) drops its co-teacher row together with the class. Verified independently: `mode ⊆ enrollable` (the mode is a raw value of some row), so per-row keeps a strict superset of what mode gating kept; the `TestBlendSuppressionByRosteringScope` fixture (`conftest.py:597-613`, MT100/101/102 = one row each at grades 1/2/3) really does flip; blend D really does survive Shape 2/3 on `_MODE_MASKED_STUDENT` (`test_class_rostering_grades.py:143-160`). Six findings below; two are substantive, one is a live counter-example to the headline claim.

#### 0. The dispositions are NOT honest — 7 of 9 are marked "Applied" but the body still carries the original text

Verified line by line: **#1** `:174` still reads "**ZERO behaviour change**" (the retitle exists only in the disposition cell). **#2** slice 1's affected-files at `:183` lists no doc at all — no ROADMAP strike, no DECISIONS line. **#3** `:152` still puts the warning inside `create_name` (per blend, i.e. 411×) with no resolve-once wording. **#4** `:187` still asserts "**no `ARCHITECTURE_TREE` change**". **#7** `:234` still says the vacuum happens "**under this very change**" — the claim the table accepts as *my error*. **#8** no `caplog` row exists in Test strategy §3. **#9(b)** `:231` is still the absence sweep ("no module outside `grades.py` contains…"), not the positive-count form. And the Risks table still carries **both** rows the table says were superseded: `:195` demands "a `Class ID` set-diff pre-release" (contradicting `:65`) and `:201` is the Path-2 row disposition #9(a) says was removed. A disposition table that says "Applied" over an unedited body is the same over-claim family the gate already flagged three times. **Required:** apply them in the body, or relabel the column *"to apply at Stage 4"* and say so.

#### 1. "Strictly subtractive in ANY config" is true only under an INVARIANT the plan never states — and the natural implementation breaks it

`:139` justifies subtractivity with the wrong premise ("per-row keeps a superset of mode gating"). That property is real but does **not** imply subtractivity — mode gating also kept a superset of *nothing* and still re-keyed. The load-bearing premise is **row-set identity with `split_by_homeroom_grades(keep="subject")`**: `enrollable` must contain the CEDS grade of *every* schedule row those sections contribute to the subject split.

The plan says `detect()` "builds a second map **alongside the mode map**". The mode map is `blended.py:389`: `schedule_df[[MT, "grade"]].dropna()`. Build the sibling the same way and **the claim fails**:

- Blend of `MT1` (rows `"03"`, `"03"`, NaN grade) + `MT2` (`"04"`), default unscoped config, `homeroom_grades` = KG–07.
- Today: `split_by_homeroom_grades` maps NaN → `grade_to_ceds(NaN)` = `"UG"` (`grades.py:102-103`, `:237`), `"UG" ∉ homeroom_grades` ⇒ **the row is kept**, gets the blended Class ID, and the blend has a real student.
- With a `dropna`-built per-row map: `enrollable = {"03","04"}` ⇒ suppressed. The NaN row still survives the subject filter, `assign_class_ids` finds no `class_map` entry and falls back to `MT1_2026`.
- Net: **`Classes.csv` GROWS by a row, a live student's Class ID is RE-ASSIGNED, and a blend with a student was suppressed** — counter-examples to "strictly subtractive", "no re-key" *and* "necessary and sufficient", in the *default* config. Empty-string grades are safe (`dropna` keeps `""`); real CSV blanks read as NaN, so this is the realistic shape.

**Required:** (a) state row-set identity as the premise, in the plan and in a code comment; (b) forbid `dropna()` / `if grade:` in the per-row builder — the same `grade_to_ceds` over the same rows, blanks included; (c) build **both** maps in ONE builder from ONE `pairs` frame (returning e.g. `SectionGrades(mode, per_row)`) so they cannot drift — this also fixes the 8-parameter `_register_blends` signature; (d) a test row with a blank/NaN grade inside an otherwise all-homeroom blend, asserting the blend SURVIVES and no `MT#_<year>` class appears. **Trap to name while doing (c):** do not unify by taking the mode over CEDS values — the current semantics is mode-over-raw-then-convert, and merging `"3"`/`"03"` before the mode can change the mode grade, i.e. change `validate`/`get_grade_range`, which this design promises are untouched. Also state the standing premise that the Classes-entity and Enrollments-entity `student_schedule` are the same file (`myedbc_mapping.yaml:168`/`:193`) — subtractivity is relative to that.

#### 2. Residual #2 is NARROWED, not CLOSED — `validate()` still reproduces its exact sentence

`validate` (`blended.py:331-335`) qualifies on the MODE set. Two sections whose modes coincide (both `06`) where one carries a grade-10 pupil produce **no blend at all**, and that pupil lands in the plain per-section class — verbatim ROADMAP:115's residual-#2 wording ("a section whose mode is out-of-scope but which carries minority in-scope students … those students land in a per-section class instead"). What closed is the *gate's* mode masking. **Required:** reword `:142` to "closed AT THE GATE" and keep a residual line naming `validate()` as the surviving mode dependency. The Path-2 residual **is** genuinely gone as a *consequence of this change* — but Path 2's absent `generate_class_id` fallback is a pre-existing gap this plan discovered; put it on ROADMAP rather than deleting it with the Risks row.

#### 3. The MODE/per-row split DOES create a partner-visible incoherence — and it is in the plan's own flip set

`get_grade_range` stays on MODE, so Shape 3 (homeroom 07–09, class scope 07–12) now ships blend D as `BLENDED_…` named **"… (05/06) 2026"** whose only enrolled student is the grade-10 `_MODE_MASKED_STUDENT`. `Classes.csv`'s `Grade` cell is `""` for every blend (`classes.py:359-360`, `:288`), so **the name is the only grade signal the partner gets**, and it excludes the grade of the sole occupant. On the DEFAULT path this is pre-existing and deliberately preserved (that is what "subtractive" buys). On a SCOPED config it is **new** — 0042 suppressed that class. **Required:** name it in Non-goals + ROADMAP as the residual being accepted in exchange for dropping the re-key; make the Shape-3 rewrite (`test_the_wholly_unrostered_blends_are_suppressed`, `test_mode_masked_section_falls_back_…`) assert the misnamed survivor *deliberately*, with the reason; and keep the partner docs from claiming a blend's name states its grades. If the owner wants the name fixed (range = `enrollable ∩ rostered`), that is a **separate slice** — it renames classes on the default path and re-opens the golden.

#### 4. The measured 9-red set is structurally BLIND to the one test this delta most likely breaks

`tests/test_blended_classes.py:462-466` asserts `inspect.getsource(blended_module).count("grade_to_ceds(") == 1` — today's single site is `blended.py:328`. A **load-time pytest plugin cannot perturb module source**, so that pin passed by construction in the measurement and the "9 failed" figure says nothing about it. The per-row builder needs a second conversion: written `{grade_to_ceds(g) for g in …}` the count becomes 2 → a **10th red**; written `.apply(grade_to_ceds)` it stays green while the pin's stated intent ("a new call site is a new spelling") is defeated — a vacuous green under this repo's own rule. **Required:** list it as a deliberate flip with its new asserted count, re-author it to pin intent (count the derivations and name them), and record in Test strategy §1 that source-inspecting/AST pins are outside the simulation's reach (this repo has three: `test_blended_classes.py:465`, `test_identity.py:193`, `test_ui_flet_identity_gate.py:335`).

#### 5. Performance is back IN scope — `:166`'s "**Out:** performance" is now false

The delta adds a per-section aggregation over the **largest frame in the pipeline** (SD40: hundreds of thousands of schedule rows). Not a blocker, and no new complexity class (`_build_grade_map` already groups the same frame), but the naive shape is a Python call per row plus a lambda per group. **Required:** move `performance` to LIGHT-in-scope and pin the cheap shape — `schedule[[MT, "grade"]].drop_duplicates()` first (dedup keeps NaN, so it composes with finding 1), convert the small deduped frame, then `groupby(MT).agg(set)`. Memory is bounded by sections × distinct grades (trivial); say so rather than leaving it unasserted.

#### 6. "Necessary AND sufficient modulo student activity" is imprecise in two ways

Necessity holds **only given finding 1**. Sufficiency's real modulus is the whole active roster: `filter_to_active` (`base.py:281-293`) filters on `context.active_student_ids`, which embeds enroll-status **and** `student_rostering_grades` **and** schedule rows referencing pupils absent from the demographic file — not "activity". Precise form: *the gate is exactly the subject path's class-EXISTENCE condition; whether an in-scope row becomes a student ENROLLMENT additionally depends on `filter_to_active`.* Second: the "one-line extension" at `:144` is not one line — `filter_to_active` fails **safe** on an empty roster (keeps everyone), so intersecting the gate with `active_student_ids` would fail **closed** (every blend suppressed when Students is disabled or ran late). Reword both.

#### Sizing / completeness check (re-gated)

- **Slice 1 — OK, unchanged by the delta.** No per-row work lands here. It still must absorb dispositions #1/#2 in the body (retitle + its own ROADMAP/DECISIONS discharge) or it lands with a stale tracked bug.
- **Slice 2 — OK, no split, but now firmly at the top of the range.** It gains: the single `SectionGrades` builder + threading, the `_register_blends` docstring rewrite (`blended.py:210-221` states the inverted rule verbatim and is the invariant's local carrier — name it, don't leave it implied by "gate + log"), the source-pin re-authoring, the blank-grade test, the naming residual, and two ROADMAP amendments. Keep the fixed intra-slice order (code → golden → tests → docs → release note). **Split only if** the owner asks for the grade-range fix from finding 3 — that is its own slice.
- One coverage note for slice 2: after the flip, `TestShapeDefaultUnchanged`'s **docstrings** (`test_class_rostering_grades.py:302-310`, "exactly the studentless blend plan 0043 will remove", "the only proof the suppression rule does not fire") become false — blend D survives because it has an in-scope pupil, not because the gate is closed. The class name may stay; the docstrings must be rewritten, and the plan should note that this corpus then contains **no** default-path suppression case (that coverage moves to `test_blended_classes.py` + the golden).

#### Harness impact (re-gated)

- **`docs/claugentic-INVARIANTS.md` — a NEW entry, not just the 0042-1b amendment already required.** Finding 1's row-set identity is textbook invariant shape: *the blend-suppression gate's grade basis must be derived from the same rows, with the same null handling, as `split_by_homeroom_grades(keep="subject")`* — invisible from either call site, and its breach is silent `Classes.csv` growth + a live re-key with a green golden.
- **`docs/claugentic-DECISIONS.md` — five lines, not four.** The plan's four plus: the mode-named / per-row-gated survivor (finding 3) is accepted deliberately, in exchange for eliminating the re-key.
- **`docs/claugentic-ROADMAP.md` — `:115` needs more than the `:119` amendment already listed.** Its residual-#2 clause becomes "narrowed at the gate, alive in `validate()`", and its "two things to flip deliberately" names `TestShapeDefaultUnchanged` as a pin that must go RED — measured GREEN under per-row (the `ARCHITECTURE_TREE:182` twin is already in disposition #4; this one is not). `:117` is already correct and needs no edit.
- **`docs/claugentic-standards/CANDIDATES.md` — one addition to the lesson already staged:** *a simulated/monkeypatched flip measurement cannot see tests that inspect module SOURCE or AST — enumerate those pins by hand before quoting a red count as complete.*
- **No new STANDARD, no new agent.**

## Spec  _(Stage 4)_

### In plain English — read this first

**What this builds.** DistrictSync currently sends SpacesEDU a small number of classes that have a teacher and no students at all. They happen when one teacher runs two split-grade sections at the same period and *every* pupil involved is in a grade that gets rostered through their homeroom instead — so the blended class it creates can never have anybody in it. The pupils are already correctly rostered in their homeroom classes, so nothing is missing; there is just a junk class beside the real one. This stops producing them.

**What "done" means for you.** Districts stop receiving empty duplicate classes. Nobody loses a class they should have: a blend that mixes a homeroom grade with a timetabled grade still ships, carrying its timetabled pupils, exactly as today. In the test district this removes precisely one class row and one teacher row and touches nothing else — and that exact two-line diff is the pass condition, not a summary of it.

**What you're accepting.**
1. **Seven districts' `Classes.csv` gets smaller** on the first run after upgrade. No importer re-confirmation is needed (nothing about `Classes.csv` was ever confirmed with the partner), and you've ruled that row-set changes are MINOR on that basis.
2. **A big district's first manual Convert may refuse to write until an admin ticks the anomaly box.** That's existing behaviour working correctly, but it will generate support calls if the release note doesn't warn them. Scheduled runs are unaffected in outcome but will show a WARNING verdict on Home once.
3. **One known cosmetic wrinkle, deliberately left:** on a district that uses grade-scoped rostering, a surviving blend's *name* can show a grade range that doesn't include its only pupil's grade. Fixing it means changing how blend names are built, which would split this work in two. Flagged for your call.
4. **Two things this does not fix,** both already tracked: a blend whose pupils are all withdrawn still ships empty, and two sections whose most-common grades collide still never merge.

---

### Slice 1 — groundwork (no change to any currently-succeeding output)

**`src/etl/transformers/grades.py`**
```python
def timetable_rostered_grades(
    homeroom_grades: Sequence[str],
    *,
    timetable_scope: Optional[set[str]],
) -> set[str]:
    """The CEDS grades that EFFECTIVELY receive subject (timetable) rostering.

    Derived, never configured — there is no `timetable_rostered_grades` config key.
    Contrast `resolve_timetable_scope`, which reports what a district CONFIGURED.
    Unifies the grade VOCABULARY only; it does not make two callers agree about
    which ROWS they apply it to (see INVARIANTS: row-set identity).
    """
```
- `split_by_homeroom_grades(keep="subject")` masks on `isin(timetable_rostered_grades(homeroom_grades, timetable_scope=timetable_scope))`, replacing the two-branch `~isin`/`isin`. **The `keep="homeroom"` + `timetable_scope` `ValueError` guard (`:227-234`) is untouched** — the unification makes it look redundant and it is not.
- `resolve_timetable_scope` docstring line 1 → *"The CEDS grades a district CONFIGURED for subject rostering, or None when it configured none."* (de-collide; today it is near-verbatim the new function's).
- Module header `:10-21` re-indexed to name four things, not two.
- `why` comment at `:103`: the `"UG"` fallback **must remain a value in `CEDS_MAPPING`**, or masking on `CEDS_GRADE_CODES` silently drops unknown-grade rows.

**`src/etl/transformers/blended.py`** — resolve the course-code column **once**, in `_register_blends` before the session loop, and pass it down; log **one** WARNING there, never per blend:
```python
def create_name(self, session_group, field_map, grade_str, course_title_map, context,
                *, course_code_col: Optional[str]) -> str:
```
`course_code_col` = `COURSE_CODE` if present, else `DISTRICT_COURSE_CODE` (the schedule-fallback shape — `classes.py:306` already renames it on the subject path), else `None` ⇒ **skip the course segment entirely**, matching the sibling guard at `:357-363`. Do **not** substitute `"Unknown Course"` (that is the per-*code* fallback, a different question).

**Tests** — `test_class_rostering_grades.py` (helper rows beside `TestResolveTimetableScope`) · `test_property_based.py` (`@given(st.text())` → `grade_to_ceds(x) in CEDS_GRADE_CODES`, plus `None`/`NaN`/`pd.NA`/`""`) · `test_blended_classes.py` (positive-count DRY pin; `create_name`: alias present → real titles · absent → skipped segment + exactly one WARNING · no-teacher-and-no-course-code → exact name `"(01/02) 2025"`).

**Docs in-slice** — `ROADMAP` strike + `DECISIONS` line (skip-not-substitute, and why it declines the roadmap's recorded fix) + `ARCHITECTURE_TREE:27`.

**Acceptance** — (a) golden **byte-identical**; (b) full suite green with **zero** flips; (c) `test_blended_classes.py:462-466`'s `count("grade_to_ceds(") == 1` resolved by routing through a helper, **not** by relaxing the assertion.

---

### Slice 2 — the suppression

**`src/etl/transformers/blended.py`**
```python
@staticmethod
def _build_enrollable_grade_map(schedule_df: pd.DataFrame) -> dict[str, set[str]]:
    """MT ID -> every CEDS grade present in its schedule rows.

    Sibling of split_by_homeroom_grades, NOT of _build_grade_map: NO dropna
    (grade_to_ceds(NaN) == "UG", which is timetable-side and a real student),
    and grades derived through grade_to_ceds. Row-set identity is the invariant.
    """
```
- One `groupby` pass beside the existing `_build_grade_map` call in `detect()`; result threaded to `_register_blends`. No per-blend rescan, no row-wise `apply` inside the session loop.
- Gate, unconditional, before the first `result.*` write:
```python
rostered = timetable_rostered_grades(homeroom_grades, timetable_scope=timetable_scope)
enrollable = set().union(*(enrollable_map.get(m, set()) for m in group[MASTER_TIMETABLE_ID].unique()))
if not (enrollable & rostered):
    suppressed += 1; ...; continue
```
- Log branches on `timetable_scope is None` — *"every grade in this blend is a homeroom grade"* vs *"outside the configured rostering scope %s"* — so the preserved `None` has a live reader.
- `validate()` / `get_grade_range()` **keep the MODE map**.

**Golden re-freeze** — regenerate through the same `bundle_mappings_dir`/`user_mappings_dir` patches `test_regression_sd74.py:54-58` uses, **never** the command documented at `generate_synthetic.py:10-14` (it loads the *live* config, whose dates auto-derive, making the golden time-dependent and reddening `test_classes_have_fixed_dates`). Fix that docstring in the same commit.

**Tests** — the 9 flips in §1 (+ the likely 10th) · **row-set identity** (NaN-grade blend survives; no fallback class; no ID re-assignment) · both `caplog` branches · positive twins with `assert not enrollments.empty` · `test_transform_classes.py:88` **scenario-rewritten** (straddling blend, in-scope pupils inactive) so the `_emit_missing_blended_classes` orphan guard survives and residual #1 becomes executable · the `contract_version` ↔ changelog parity row.

**Docs** — `output-contract.md` (`:17` rule · `:246` correction · `:248`/`:250` narrowed · `emitted_by` · changelog **after** the identity test is green) · **`partner/how-classes-work.md` + `partner/faq.md`** (both teach `Grade 1/2` and `(03/04)` — cases this change deletes; replace with a surviving 07/08 blend and add the suppression step) · `adding-district.md` · `architecture.md:122` · `index.md:73` · `ARCHITECTURE_TREE:182` · `INVARIANTS` (new row-set-identity entry + amend `:71`) · `ROADMAP` (`:115` amended not struck — residual #2 is narrowed; `:119`'s remedy corrected) · 5 `DECISIONS` lines · `CLAUDE.md` · release note (three anomaly surfaces).

**Acceptance** — (a) golden diff **exactly** the two lines in §4, `Students`/`Staff`/`Family` 0 changed; (b) the row-set-identity test green; (c) the flip set is exactly the enumerated one — anything else red is a genuine finding; (d) `test_contract.py:803` and `test_zero_orphan_enrollments.py` green.

**Intra-slice order** (the doc tail is the part most likely to be skimped, and it is the partner-facing half): code + log → golden re-freeze → tests → docs → release note.

### In-scope standards dimensions & target bar
`api-and-contracts` (versioning rule written, not inferred) · `data-and-persistence` (row-set change, referential integrity) · `reliability-resilience` (suppression cannot orphan; guard fails loud-then-degrades with a log) · `testing` (no vacuous greens; every absence has a positive twin; the golden diff is the oracle) · `maintainability-structure` (one spelling, locked by a positive-count pin) · `observability-ops` (one warning per run, PII-free counts) · `performance-efficiency` (one extra `groupby` over the largest frame, no per-blend rescan). **Bar: meet fully.** Out: `product-ux`, `security`.
