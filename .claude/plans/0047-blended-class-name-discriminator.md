# 0047 — Give a blended class a name that identifies it

**Status:** verified (Stage 7 closed); awaiting CI + merge
**Raised by:** SD54 partner question, 2026-09-16 — "are these two teachers' blended classes working correctly?"

**Measurement provenance.** Every count below is measured on the SD54 delivery at `roster-validation/partners/sd54/20260916/` (`Classes.csv`, 318 rows; `Enrollments.csv`; `Staff.csv`), which is correctly not in this repo. Counts are re-derivable by grouping `Classes.csv` rows whose `Class ID` starts with `BLENDED_` by `Name`, splitting the ID on `_` as `BLENDED, school, teacher, term, semester, day, period, year`. Stage 7 should re-derive rather than re-trust.

## Problem

The blends themselves are correct. Their **names** are not distinguishing.

`BlendedClassDetector.create_name` composes `<course titles> (<grades>) <year>` and nothing else. Three defects follow:

1. **No session discriminator.** The regular path ends a name with the section letter (`… (001) 2027` / `… (002) 2027`); the blended path has no equivalent, so two blends of the same course pair by the same teacher in different blocks are indistinguishable. **68 of 98 blended classes (69%) share a display name with another blend**, and 16 of the 29 teachers holding blends have 2+ identically-named ones — against 4 of 220 on the regular path. One teacher has five blends rendering as two names (three identical, two identical).
2. **No teacher name.** `create_name` adds it only when the configured `teacher last name` column is already on the frame it groups over. That frame is `ClassInformation` (or the deduplicated schedule fallback) — never the schedule join the regular path uses. In-repo proof: golden `tests/snapshots/output/Classes.csv:51` `Hill Henry Music 10 (B) 2026` (regular, teacher present) against `:54` `Business 11 / PHE 9 (08/11) 2026` (blended, teacher absent), and `tests/snapshots/input/ClassInfoEnhanced.txt` carries no teacher-name column.
3. **Truncation eats the identifying tail.** `truncate_name` cuts the *assembled* string at 100 chars, and the unbounded joined course titles sit in the middle, so the `(grades) year` suffix is what gets dropped: **22 of 98** blended names truncate, and all 22 lose their grade token. Because `Grade` is deliberately blank on blended rows (pinned, `tests/test_transform_classes.py:218`), those 22 carry **no grade signal anywhere**.

**The three are one defect, not three features.** With (1) alone and today's whole-string truncation, the new discriminator would itself be eaten on exactly the 22 names that truncate — so (3) is load-bearing for (1), not polish on top of it. And (1) alone does not finish the job either:

| Name composition | Distinct names | Names reused | Classes sharing a name |
|---|---|---|---|
| today | 50 | 20 | **68** |
| + block discriminator (A1) | 92 | 2 | **8** |
| + teacher name (A1+A2) | 98 | 0 | **0** |

The 8 residual collisions under A1 alone are 6 teachers running the same multi-disciplinary block at one school in the same slot, plus 2 PHE teachers — **different teachers, same course pair, same block**, which only the teacher name separates. A2 is load-bearing, not consistency polish.

Not in scope as a defect: the blank `Grade` column is intended and stays.

## Goals / Non-goals

**Goals**
- A blend's name identifies it **as finely as `session_key` distinguishes blends** — i.e. exactly as finely as its own Class ID. No claim is made beyond that; see the Risks row on the ROADMAP's session-key coarseness item.
- A blend carries a teacher name wherever the regular path does.
- The parts that identify a class survive the 100-char cap.

**Non-goals**
- No change to which blends are *detected*, suppressed, or how Class IDs are formed. Name text only.
- **No narrowing of `session_key`.** The 2026-09-16 ROADMAP item on no-rotation exports is untouched by this slice.
- No new config key. This is a defect in a derived display value, not a district preference — an opt-in would leave 19 districts with the broken names.
- `Grade` stays blank on blended rows.
- No change to the regular-class name composite.

**Retires nothing.** No code, test, doc section or config key is removed by this slice.

## Approach

### A1 — Block discriminator from the components that already define the blend

`_add_session_key` joins `[SCHOOL_NUMBER, teacher_id, term, semester, day, period]`. School and teacher are constant within a blend and already in the Class ID, so the discriminator is built from the **remaining four**.

The four names are lifted to **one module constant** that `_add_session_key` and the discriminator both read, so a component added to the session key follows into the name automatically. No `components` parameter — there is no second caller.

**Absent or blank → omitted.** `_add_session_key` only stringifies components `available` in the frame, so `group["period"]` on a district lacking the column would raise `KeyError` and kill the Classes entity. The discriminator therefore intersects the constant with `group.columns` exactly as `_add_session_key` does, then drops blanks by `.strip()` (those columns are already `fillna("").astype(str)`-ed). All four absent or blank → segment omitted and the name is **byte-identical to today's**. The four existing `TestCreateBlendedClassName` cases pass groups carrying none of the four, which is why they stay green.

Every row in the group shares these values by construction for the *available* subset — they are the group key — so taking the first row is safe.

**Formatting: `(Block <values>)`, values joined by a space, never labelled individually** (owner decision, 2026-09-16). The components' semantics differ per district — SD54's school 5454011 numbers days and letters periods, 5454013 does the reverse — so a "Day A Period 1" rendering would assert an order the data does not guarantee. But bare values fail on SD74, whose components are bare integers: `… (1 2 1 2) (08/11) 2026` puts two adjacent numeric groups side by side and a partner reads the first as a grade list. The `Block` literal removes that ambiguity for 6 characters without asserting which token is which, and leaves the existing grade group untouched.

### A2 — Teacher name from the schedule map first, frame column as fallback

`_load_reference_frames` **already loads the schedule**, and the schedule is the frame the regular path reads `Teacher Name` from (base declares `"teacher last name": "Teacher Name"`, `config/mappings/myedbc_mapping.yaml:183`, inherited by all 20 configs; the staff merge contributes only `LAST_NAME`, which no bundled config selects). So build a teacher-id-to-name map off it, as `_build_course_title_map` is built off `course_df`.

**Precedence is map-first, frame column as documented fallback.** No bundled `ClassInformation` carries a teacher-name column, so a frame-first rule would be a second source of the same fact whose only live consumers are the unit tests. Map-first was measured to cost nothing: the `DataTransformer` facade passes an empty map, so the frame-supplied cases stay green and now prove the **fallback** rather than a preference.

**The join must be normalized on BOTH sides.** `_load_reference_frames` normalizes only `MASTER_TIMETABLE_ID`, never the teacher id, while `_run_blended_detection` normalizes ClassInformation's teacher id via `normalize_id_series`. A map keyed on the raw schedule id and looked up with the normalized class_info id misses silently — and the miss is indistinguishable from "no teacher available", so the fix would not land on the district it was written for. Apply `normalize_id_series` on both sides, pinned by a case with a padded id (`"T010 "` vs `"T010"`) that is RED without it.

Absent column or unknown id → segment omitted, as today.

### A3 — Budget the course segment, and let `truncate_name` own the cap in every branch

`<Teacher> <Course titles> (Block <block>) (<Grades>) <Year>` — the regular composite's word order, plus the grade range.

The fixed parts are short and bounded; the joined course titles are the only unbounded part. Compute the fixed parts, give the course segment the remaining budget, truncate **it** at a word boundary, then assemble and pass the result through `truncate_name` unconditionally. The cap is therefore guaranteed by the one call that already owns it, in every branch.

**Budget floor of 10.** Measured: `truncate_name("AAAA BBBB CCCC", 2)` returns 12 chars — `trunc_len = max_len - 3` goes negative and the guarantee inverts. `tests/test_property_based.py:172` proves `len(result) <= max_len` only for `max_len ∈ [10, 200]`, and this is the first caller passing a computed budget. A budget below 10 is reachable (a long teacher name plus a 12-grade blend puts the fixed parts at 97-98 chars), so **budget < 10 → drop the course segment entirely**, staying inside the proven band. `truncate_name` itself is not changed.

The budgeting stays inline in `create_name` over the one named segment. If it ever grows into a general segment-priority mechanism, that is the over-build and the right correction is the simpler reordering instead.

## Affected files

| File | Change |
|---|---|
| `src/etl/transformers/blended.py` | session-component constant; teacher-name map; block discriminator; `create_name` composition + budget; `_register_blends` threads both |
| `src/etl/transformer.py` | `DataTransformer` facade forwards to `create_name` with explicit args (`:210-217`); passes an empty teacher map (it holds no schedule) |
| `src/etl/transformers/naming.py` | none — `truncate_name` reused as-is |
| `tests/test_blended_classes.py` | new cases; **re-pin `:866`**, an exact-equality assertion outside `TestCreateBlendedClassName` whose inline frame *does* carry the four components, without weakening what it pins (course-code skip-don't-substitute + once-only warning) |
| `tests/snapshots/output/Classes.csv` | SD74 golden: 4 blended rows gain teacher + block |
| `docs/developer/output-contract.md` | row-VALUE tier rule; 2.4.0 → 2.5.0 front matter **and** a new row in the doc's own changelog table; `emitted_by` refresh; blended composite line |
| `docs/claugentic-DECISIONS.md` | one dated line |
| `CHANGELOG.md` | `[Unreleased]` → `Fixed` |

No source file added/moved/removed ⇒ no `ARCHITECTURE_TREE` entry.

**Confirmed safe — nothing keys off the blended `Name`.** Classes dedup is `drop_duplicates(subset=["Class ID"])`; the quality dup key for Classes is `["Class ID"]`; `compute_anomalies` is row-count + vanished-entity only; Enrollments never reads Name; and `_emit_missing_blended_classes` and `_assign_class_names` read the **same** `metadata["Name"]`, so the two paths stay byte-identical. **No row set moves.**

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Every district's blended names change | Intended — the names are defective everywhere. **Verified on our side:** Class ID is untouched, so nothing re-keys; IDs are byte-stable across SD54's three drops. |
| *Unverified:* how the importer treats a changed Name | A claim about trust-chain link 1, which the contract says is not readable from here. Route to the owner rather than assert it; does not change the MINOR grade. |
| Name uniqueness inherits `session_key`'s coarseness | The ROADMAP item dated 2026-09-16 records that `session_key` cannot distinguish sections on a no-rotation export (`Day` always `"1"`). The discriminator is built from four of that key's components, so on such an export it prints a constant — noise, not signal. Explicitly **not** narrowed here. |
| SD74 golden churn hides a regression | 4 blended rows change; the other 151 must be byte-identical — diffed by hand in the PR body. |
| District with no term/day/period columns | Segment omitted, name byte-identical to today. No fabrication, no `KeyError` (A1). |
| Contract MAJOR? | No. Column set, order, filename, encoding unchanged, and the Name row is `pending owner confirmation`, so no owner-**confirmed** row is invalidated. MINOR — under a row-value tier this slice must **author**, since the doc's written rules cover only column-shape and row-set. |

## Test strategy

Kept deliberately small — one case per independently-failing behaviour.

- **Anti-vacuous twin:** two same-course blends by one teacher in different blocks now produce **different** names (the defect), not merely "a discriminator appears".
- All four components absent → name byte-identical to today's text.
- Teacher name resolved from the map when the frame column is absent; padded-vs-normalized id (`"T010 "` / `"T010"`) resolves — RED without the two-sided normalize.
- Budgeted truncation retains block + grades + year and never exceeds 100; budget < 10 drops the course segment.
- Regression: the four existing `TestCreateBlendedClassName` cases pass **unchanged**, now proving the frame-column fallback.
- SD74 snapshot + full suite + 80% gate + ruff + ruff format + mypy + bandit + `validate-config`.

## Decomposition

ONE slice — one function's composition plus its tests, the golden and the contract statement. Vertically complete, no half state. The golden regeneration is the only irreversible step and the owner has seen the rendered names in advance.

---

## Review

**Stage 3 adversarial plan review — 2026-09-16.** RUNNING AS: Opus 4.x (same family as the planner on a single-session run — this is a role/clean-context check, not an independent oracle).

### Verdict: **CHANGES REQUIRED**

The diagnosis is right and the three defects are real — all three mechanisms confirmed in the code, and the SD74 golden corroborates #2 on evidence the plan does not cite. The approach is sound in shape. Ten changes are required: two are correctness bugs the plan would ship (R2, R5), two are premises that are true but under-argued in a way that costs the fix its value (R3, R4), one is a goal the repo's own ROADMAP contradicts (R1), and the rest are contract/accounting gaps.

#### Confirmed against the code (so the next round need not re-litigate)

- **Problem #2's premise is TRUE.** Base config declares `"teacher last name": "Teacher Name"` (`config/mappings/myedbc_mapping.yaml:183`), inherited by all 20 configs; `ClassTransformer._assign_class_names` resolves that key and `generate_class_name` reads it off `merged`. The staff merge contributes only `LAST_NAME` (`classes.py::_merge_course_and_staff`), which no bundled config selects — so the regular teacher name really does come off the **schedule** frame. Proof in-repo, uncited by the plan: `tests/snapshots/output/Classes.csv:51` `Hill Henry Music 10 (B) 2026` (regular, teacher present) vs `:54` `Business 11 / PHE 9 (08/11) 2026` (blended, teacher absent), and `tests/snapshots/input/ClassInfoEnhanced.txt` carries no teacher-name column. Cite this — it is the only reproducible evidence in the repo.
- **Nothing keys off the blended `Name` string.** Classes dedup is `drop_duplicates(subset=["Class ID"])` (`classes.py::transform`); the quality dup key for Classes is `["Class ID"]` (`src/quality/report.py::_check_duplicates`); `compute_anomalies` (`src/etl/pipeline.py:439`) is row-count + vanished-entity only; Enrollments never reads Name; and `_emit_missing_blended_classes` and `_assign_class_names` read the **same** `metadata["Name"]` dict, so the two paths stay byte-identical and the outer dedup is unaffected. **No row set moves.** Confirmed — keep this as a stated premise.
- **Stability (A1) is stronger than the plan argues.** `blended_id = f"BLENDED_{session_key}_{context.school_year}"` (`blended.py:307`) and `session_key` already contains all four components — so the discriminator is **exactly as stable as the Class ID**, and churn in term/day/period would already be re-keying classes today. Make that argument explicitly; it is free and it is the strongest one available.
- **Checked and dismissed, recorded so it is not re-raised:** `_add_session_key`'s `.fillna("").astype(str)` renders a float column as `"1.0"`, which would become partner-visible once these values enter the name. Not reachable — the extractor reads every GDE with `dtype=str` (`src/etl/extractor.py:252,346`), so only a hand-built frame can produce it. No change needed.
- **ARCHITECTURE_TREE:** correct as stated, no entry needed (no source file added/moved/removed).

#### Required changes

1. **Scope the goal — the repo's own ROADMAP contradicts "for every district."** `docs/claugentic-ROADMAP.md` (item dated **2026-09-16**, the same day as this plan, same area) records that `session_key` "cannot distinguish sections when an export has no day rotation (`Day` always `"1"`) — SD51-shaped secondary timetables produce false merges, and the fallback frame … shares the SAME key". The discriminator is built from four of that key's six components, so name uniqueness inherits the key's coarseness exactly: on a no-rotation export the new segment prints a **constant** — noise with no signal — and two blends a partner sees as different still share a name. Restate the goal as "identifies a blend as finely as `session_key` distinguishes blends", add a Risks row naming the ROADMAP item, and say explicitly that this plan does not narrow the key. (The other residual — same teacher, two schools, same slot, school omitted from the discriminator — was checked and dismissed: physically unreachable.)

2. **A1 must branch on ABSENT columns, not just blank values — the plan's own regression claim depends on it.** `_add_session_key` only stringifies the components that are `available` in the frame (`blended.py:190-195`), so `group["period"]` on a district whose export lacks the column raises `KeyError`, and the four existing `TestCreateBlendedClassName` cases pass groups carrying **none** of the four. "All four blank → segment omitted" must read **"absent or blank"**, derived from the intersection of the four with the frame's columns — the same `available` computation `_add_session_key` does. Also correct the supporting sentence: "every row in the group shares these values by construction (they are the group key)" is true only for the *available* subset.

3. **Normalize the teacher-id join on BOTH sides, or A2 silently fails to deliver.** `_load_reference_frames` normalizes only `MASTER_TIMETABLE_ID` (`blended.py:135-136`) and never the teacher id, while `_run_blended_detection` normalizes ClassInformation's teacher id with `normalize_id_series` (`classes.py`). A map keyed on the **raw** schedule id and looked up with the **normalized** class_info id misses on any whitespace/format difference — and the miss is indistinguishable from "no teacher available", so the fix would silently not land on the district it was written for. This is exactly why `_create_subject_classes` normalizes the schedule's teacher id before the staff merge. Prescription: `normalize_id_series` on both sides (the codebase's single spelling), plus a pin using a padded id (`"T010 "` in one frame, `"T010"` in the other) that is RED without it.

4. **Invert the precedence: schedule map first, frame column as fallback — it is free, and "frame wins" is a second source of the fact Goal 2 says must be single.** No bundled ClassInformation carries a teacher-name column (SD74's `ClassInfoEnhanced.txt` header; `tests/test_contract.py::_write_class_info_empty`), so the branch's only live consumers are the unit tests and the schedule-fallback path — where the frame **is** the schedule and both sources agree, so there is no divergence there (that answers the fallback-inconsistency question: not a real inconsistency *today*, but only because the branch is dead). The bullet's stated justification — "a district whose `ClassInformation` carries the teacher name is unaffected" — describes a population of zero; the real reason given is that the existing tests keep passing, which is a rule written for the test suite. **Measured: inverting costs nothing.** The facade holds no schedule and would pass an empty map, so `test_spaced_key_name_config_drives_teacher_column` (`== "Nguyen English 1 / English 2 (01/02) 2025"`) and `test_neither_column_skips_the_segment_and_warns_exactly_ONCE` (frame-supplied "Cole"/"Diaz") both stay green on the fallback. Adopt map-first, keep the frame column as the documented fallback for "class_info has it, schedule does not", and restate the regression claim: those cases then prove the **fallback**, not that the frame path is preferred.

5. **A3's budgeted call breaks `truncate_name`'s only proven band — clamp it.** Measured: `truncate_name("AAAA BBBB CCCC", 2)` returns `"AAAA BBBB..."` — **12 chars for a 2-char budget**; overflow at `max_len` ∈ {0,1,2}. `tests/test_property_based.py:172` proves `len(result) <= max_len` only for `max_len` ∈ [10, 200] (`st.integers(10, 200)`), and A3 introduces the **first** caller passing a computed, data-dependent `max_len`. The stated fallback ("if the fixed parts alone exceed 100") does not cover a budget of 1-9, which is reachable: a long teacher name plus a 12-grade blend puts the fixed parts at 97-98 chars. Prescription: **budget < 10 → drop the course segment entirely** (matching the band the property test proves), or widen that test's lower bound and fix `truncate_name` — name which, and pin the chosen floor.

6. **The MINOR is defensible, but the rule that grades it does not exist yet — this plan must author it.** The Name row *is* `pending owner confirmation` (`docs/developer/output-contract.md:252`), so no confirmed row is invalidated and MINOR is the right answer. But the doc's stated rule (line 24) covers column set/order/filename/encoding only, and the 2026-08-14 owner ruling (line 26) adds a **row-SET** tier — neither covers a row **VALUE**. 2.1.0 is a genuine precedent (its header reads "Row values and row sets change"), but its justification grades it "MINOR under the row-set rule below" — it stretched the row-set rule without saying so, which is precisely the failure the ruling's own note warns about: "the first change to exercise the gap would otherwise have authored the rule that graded it." So: **add the row-value tier explicitly** (one sentence beside the row-set ruling, same shape: MINOR when it invalidates no owner-CONFIRMED row, MAJOR when it does), and cite 2.1.0 as the precedent it codifies. Do not lean on a rule that is not written down.

7. **Three mechanically-enforced contract edits the plan does not name.** (a) `tests/test_output_contract_doc.py::test_the_declared_contract_version_is_the_newest_changelog_row` requires a new row in the doc's **own** changelog table (lines 13-21) alongside the front-matter bump — "bump both or neither"; (b) the front-matter `emitted_by` cell (line 6) names which release emits which behaviour and is refreshed on every release touch (DECISIONS, 2026-09-16); (c) the existing "The name-config composite" section (line 260) documents only the **subject** composite while the Name row points every reader at it — the doc is already wrong for blended rows. Name (c) as the pre-existing doc defect this slice folds in (same file, same edit), not as new prose.

8. **Split the risk row that asserts an importer behaviour nothing here can verify.** "SpacesEDU updates names in place and no class is rebuilt or loses content" is a claim about trust-chain **link 1**, which the same document says "is not readable from here, so nothing in this repo can *prove* what it does". What the plan verified is **our** side (Class ID stability) — a different claim. The 2.0.0 precedent is the warning: an owner check found the *old* shape silently broken. Split the row into the verified half (Class ID untouched ⇒ nothing re-keys) and the unverified half, and route the latter either to the owner (who is the SpacesEDU team) or into the doc's open-owner-questions block (pinned verbatim by `test_the_open_owner_questions_are_stated_verbatim`). This does not change the MINOR grade; it stops an unverified importer claim from reading as verified.

9. **Affected files is short by two.** (a) **`src/etl/transformer.py`** — the live `DataTransformer` facade (`src/etl/pipeline.py:253` constructs it) forwards to `create_name` with explicit args at `transformer.py:210-217`; a new parameter breaks it, and all four legacy naming tests run **through** it. Add the row and state what it passes (an empty map) and why that is correct. (b) **at least one exact-equality assertion outside `TestCreateBlendedClassName` goes RED:** `tests/test_blended_classes.py:866` asserts `== ["Cole (01/02) 2025", "Diaz (03/04) 2025"]` over an inline class_info frame that **does** carry `term/semester/day/period`, so it becomes `Cole (1 1 1 1) …` / `Diaz (1 1 1 2) …`. That test pins the course-code *skip-don't-substitute* guard and the once-only warning, **not** naming — re-pin it without weakening what it pins. (`tests/test_blended_classes.py:828` and `tests/test_class_rostering_grades.py:523` survive: the fallback fixture carries none of the four, and the latter asserts a substring.)

10. **Show the SD74 rendering before implementation, not only SD54's.** "Joined, never labelled" is argued entirely from SD54, where the values read as `S1 A 1`. The only district this repo can actually render is SD74, whose `ClassInfoEnhanced.txt` carries `Semester/Term/Day/Period` as bare integers — so the golden's `BLENDED_7474012_T0001002_1_2_1_2_2026` becomes `… (1 2 1 2) (08/11) 2026`: **two adjacent parenthesised numeric groups**, the first of which a partner will read as a grade list. Put the four regenerated golden names into this plan now and let the owner see them; if `(1 2 1 2)` is unacceptable, that is a formatting decision, not a test edit, and it is far cheaper before implementation than after the golden is re-cut.

#### Measurements I could not confirm

`98 blended / 220 non-blended`, `68 of 98`, `16 of 29`, `4 of 220`, `22 of 98 truncate`, "all 22 lose their grade token", `Kiaya Sanderson`, and "IDs are byte-stable across SD54's three drops" are all measured against a drop that is (correctly) not in this repo. Nothing here contradicts them and they are internally consistent (68/98 = 69%; `155 golden data rows − 4 blended = 151` checks out exactly). Record the provenance in one line — which drop date and the command that produced the counts — so Stage 7 can re-derive rather than re-trust. Separately, A3's worked example shows a **46-char** course segment against its own **53-char** budget; recompute it from the real string rather than writing it by hand, since it is presented as the worst case.

#### Sizing / completeness check

**ONE slice — OK, no split needed.** With R7 and R9 folded in it is ~8 files (`blended.py`, `transformer.py`, two test modules, the golden, `output-contract.md`, DECISIONS, CHANGELOG) around one function's composition. Vertically complete: the code, its tests, the regenerated golden and the contract statement all land together, and no `TODO` or deferred half-state is implied. The SD74 golden regeneration is the only irreversible step, and R10 puts an owner-visible preview in front of it.

#### Harness impact

**Nothing lands plugin-side.** No new STANDARD, agent, or managed harness doc; the new contract row-value tier (R6) lands in `docs/developer/output-contract.md`, a project doc, and the dated entry in `docs/claugentic-DECISIONS.md` is project-local. Nothing to stage upstream from this slice.

#### Closing the gate

R2, R3 and R5 are code-correctness and must be resolved in the plan text before implementation. R1, R4, R6, R8 are wording/decision changes to this document. R7, R9, R10 are accounting. Applying all ten as written closes the gate; the next round is a **diff confirmation of this plan file**, not a re-gate.

---

## Verify  _(Stage 7 — solo adversarial audit, 2026-09-16)_

**Verdict: CHANGES REQUIRED → all four discharged in-pass; gate closed.**

The audit confirmed the behaviour, the golden (157 lines, 4 rows differ, `Name` only, other 151 byte-identical) and every gate, and independently fuzzed the cap: 20,000 randomized `create_name` calls produced **0 names over 100 chars, max exactly 100**. It found four defects, two of them **measured by mutation** rather than argued — both were tests whose docstrings claimed a pin the fixture did not make:

- **V1 / F1 — the teacher-id normalize was pinned on ONE side only.** Mutating the *build*-side normalize killed the test; mutating the *lookup*-side one **survived**, because the fixture's class-info id was already unpadded. Fixed by padding BOTH frames' ids (`["T500 ", "T500 "]`). Re-verified here: both mutants now RED, expected name byte-identical.
- **V2 / F2 — the budget floor was pinned by a test that could not see it.** `_MIN_COURSE_SEGMENT_BUDGET` could be set to **0** with all four assertions still green, because the scenario landed at budget 7 — a band where `truncate_name` is well behaved. Fixed by lengthening the teacher name to 39 chars so the budget lands at exactly **0**; the existing `endswith("2025")` now does the killing. Re-verified: floor→0 is RED. Docstring corrected — the behavioural boundary is budget `< 3`; **10 is a stay-inside-the-proven-band choice, not a behavioural threshold**, and the test pins the guarantee (the tail survives), not the number.
- **V3 / F3 — R8 had been reversed:** the unverified importer claim shipped flat on two partner-facing surfaces, contradicting this plan's own approved risk row. Both hedged to the verified half (`Class ID` untouched ⇒ nothing re-keys on our side), and the link-1 question is now **routed to the owner as Q4** under *Open owner questions* — which required the coupled `Q4_TEXT` + `_EXPECTED_QUESTION_COUNTS` entry in `tests/test_output_contract_doc.py`, since that block is pinned verbatim. That discharges **L1**, which the audit had routed to Land.
- **V4 / F4 — "always"/"never" were false above a measured threshold** (`len(teacher) + 3 × grades > 76`; year lost at a 39-char teacher name on a 13-grade blend). Both softened to the stated condition plus the degenerate case.

**Routed out, not fixed here:** **L2** (the `emitted_by` cell carries an "unreleased at the time of writing" placeholder that nothing gates — the release commit must substitute the version; worth a release-checklist line) and **L3** (`_build_teacher_name_map` resolves duplicate ids first-wins for byte-identical ids but last-wins for padding variants — deterministic, benign with real data, needs its own test, so ROADMAP not fold-in). Four findings were examined and **explicitly dropped with reasons** (`"nan"` block cell — not production-reachable, the only non-production caller being the test facade; the `len(name_parts) <= 1` fallback corner — unpinned before and after; `<Teacher last>` misnomer and the unparity'd example name — both pre-existing and mirrored in the subject composite directly above).

**Still owed at Land:** CI's own three-OS result, read and quoted (2026-07-30 land gate) — the local green is Windows-only.
