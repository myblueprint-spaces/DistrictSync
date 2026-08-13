# 0042 — `class_rostering_grades`: opt-in grade scoping for class rostering

- **Status:** Spec'd (Stage-3 gate returned CHANGES REQUIRED; all 7 applied — see Review § Disposition)
- **Resumable from:** Stage 4 spec → Stage 5 approval
- **Blockers:** none
- **Flags:** kept `"homeroom"` as sugar for "rostered == homeroom_grades" so SD83 needn't restate 13 grade codes (owner may drop it; trivially removable).
- **Disposition at close:** single slice — done or deferred per the workflow's lifecycle rule.
- **Roadmap item:** `docs/claugentic-ROADMAP.md` → Deferred tech work (0043 studentless-blend fix + the blended naming crash).
- **References:** `docs/claugentic-ARCHITECTURE_TREE.md` · `docs/claugentic-DECISIONS.md` (2026-08-13) · `docs/developer/output-contract.md` · commit `e187ac8` (the orphan-BLENDED fix this plan must not regress)

## Problem

`homeroom_grades` decides which grades get *homeroom* classes, and **every remaining grade automatically gets subject (timetable) classes and enrollments** — `classes.py:226` / `enrollments.py:186` call `split_by_homeroom_grades(..., keep="subject")`, which returns exactly the non-homeroom rows. There is no way to say "roster only these grades."

Three district shapes need it, and none is expressible today:

| # | Need | `homeroom_grades` | `class_rostering_grades` |
|---|---|---|---|
| 1 | **SD83** — SpacesEDU for K-8, myBlueprint+ transcripts for 9-12 | K-08 | `"homeroom"` |
| 2 | No homeroom rostering at all; timetable rostering for senior grades only | `[]` | `["10","11","12"]` |
| 3 | Homeroom for 7-9, timetable for 10-12, K-6 excluded entirely | 07-09 | 07-12 |

A third code path compounds it: **blended detection applies no grade filter at all** (`blended.py:57-93`). Because a homeroom-grade student never receives a subject enrollment (`enrollments.py:198`) while `_blended_teacher_enrollments` (`enrollments.py:229`) filters nothing, **a blend whose grades are all homeroom grades yields a class with a teacher and zero students.**

**Evidence and its limits — read this before citing it.** The mechanism is confirmed two ways: a purpose-built probe (a 3/4 split sharing one Homeroom value produced a correct 2-student homeroom class *plus* a studentless `BLENDED_` duplicate), and the SD74 snapshot output. **The SD74 snapshot is fabricated, not de-identified production data** — `tests/snapshots/generate_synthetic.py` ("realistic-looking but entirely fake"), `random.seed(74)`, 100 students / 10 staff / 50 sections. Its session components have almost no variety (`term` has ONE distinct value; `semester`/`day` two; `period` five), so 6 of 41 sessions collide by chance and its "blends" (e.g. `Homeroom 1 / Music 10`, 1 student) are seed artifacts, not real classes. It is therefore **valid evidence of deterministic code behaviour and of nothing else** — it says nothing about how often studentless blends occur in production. The only genuine production data point is `e187ac8`: SD40 FY2026 had 411 blended classes and 195 orphans, with no studentless breakdown available.

## Goals / Non-goals

- **Goal:** `global_config.class_rostering_grades` — the complete set of grades that receive class rostering. Absent ⇒ today's behaviour.
- **Goal:** absent key ⇒ **byte-identical output for the other 11 configs**. The SD74 golden must not move.
- **Goal:** all three shapes above expressible in config alone.
- **Non-goal:** changing `Students.csv`. Grades 9-12 MUST stay rostered — `StudentCourses` applies the zero-orphan filter against the active roster, so dropping them empties the myBlueprint+ transcripts SD83 asked for.
- **Non-goal:** filtering `Staff.csv` (owner decision — staff stay unfiltered, as in every other config).
- **Non-goal:** the general studentless-blend fix for the other six districts — partner-visible, rewrites the SD74 golden, **plan 0043**. This plan implements the identical rule **gated** on the key being set; 0043 deletes the gate.
- **Non-goal:** validating `homeroom_grades` against the CEDS vocabulary generally (pre-existing gap → roadmap). Mitigated here for configs that set the new key, via the subset rule.

## Approach

**Semantics (owner, 2026-08-13).** `class_rostering_grades` is the *complete* set of grades to roster, and **`homeroom_grades` must be a subset of it** (validated, fail-loud). That yields two derived scopes and a total rule:

- **homeroom scope** = `homeroom_grades` → homeroom classes + homeroom enrollments
- **timetable scope** = `class_rostering_grades − homeroom_grades` → subject classes + subject enrollments
- grades in neither → **nothing**
- key absent ⇒ timetable scope = "everything not in `homeroom_grades`" — **exactly today's behaviour**

`"homeroom"` is sugar for `class_rostering_grades == homeroom_grades` (timetable scope empty), so SD83 needn't restate 13 codes and the two keys cannot drift.

**The subset rule is what makes this safe and total.** Because `homeroom_grades ⊆ rostered`, filtering to rostered and then to homeroom is the same as filtering to homeroom — so **the homeroom path needs no change whatsoever.** (This also permanently resolves the advisory panel's "two dead call sites" finding: those edits aren't dead, they're unnecessary.) A district whose inherited `homeroom_grades` isn't a subset of its new list gets a **loud validation error**, forcing it to state `homeroom_grades` explicitly — including `[]` for shape 2.

**Three touch points:**

1. `grades.split_by_homeroom_grades(..., keep="subject")` — gains an optional `timetable_scope`; when given, keeps `grade_ceds.isin(timetable_scope)` instead of `~isin(homeroom_grades)`. **The scope lands INSIDE the function that already does the CEDS conversion**, so there is exactly one conversion and no ordering hazard (see the defect table). The two callers (`classes.py:226`, `enrollments.py:186`) just pass it through.
2. `blended._register_blends` — suppress a blend iff **none of its grades is in the timetable scope**, i.e. `blend_grades ∩ timetable_scope == ∅`, gated on the key being set.
3. Config + wiring: the field, its validators, `to_raw_dict`, the version gate.

**Blend-rule derivation, checked against all three shapes.** The rule is keyed to the timetable scope because that is the set whose students receive subject enrollments — so "no grade in timetable scope" means **no student CAN be enrolled** via the subject path.

**It is necessary, not sufficient — three honest qualifications (Stage-3 gate, R3):**
- **(i) The grade set is of MODE grades.** `_build_grade_map` (`blended.py:320-332`) keys each MT ID to its per-section *most common* grade. A section whose mode is out of scope but which carries **minority** in-scope students has its blend suppressed; those students are **not orphaned** (suppression precedes the `class_map` write, so `assign_class_ids` resolves the plain MT-ID class consistently in both `classes.py:231` and `enrollments.py:190`) — they land in a per-section class instead of the blend. Named, tested as shape-3 case (iv), accepted.
- **(ii) A surviving blend can still be studentless** if its in-scope students are all inactive (`filter_to_active` drops them downstream). Residual assigned to **0043**, not silently absorbed.
- **(iii) The favourable fact worth pinning:** `validate()` (`blended.py:259-269`) already requires ≥2 resolvable CEDS grades, so a blend reaching this check can never carry an EMPTY grade set — **the rule can never suppress on "grades unknown"**.

**Single-source the grade-set derivation.** `validate` (`:264-269`) and `get_grade_range` (`:271-282`) already duplicate the MT-ID→CEDS grade-set loop; the suppression check would make three. Extract `_blend_grades(group, mtid_to_grade) -> set[str]` and consume it from all three (Stage-3 gate, R4).

| Shape | Blend | Timetable scope | Rule | Outcome |
|---|---|---|---|---|
| 3 | 09/10 | 10-12 | `{09,10} ∩ {10,11,12} ≠ ∅` | survives, carrying grade-10 students ✓ |
| 3 | 07/08 | 10-12 | `∅` | suppressed — studentless ✓ |
| 3 | 05/06 | 10-12 | `∅` | suppressed — unrostered ✓ |
| 2 | 10/11 | 10-12 | `≠ ∅` | survives ✓ |
| 1 (SD83) | any | `∅` | `∅` always | all suppressed ✓ |
| absent key | 02/04 | not-homeroom | gated OFF | unchanged (0043 ungates) ✓ |

**Suppression must happen BEFORE any `result.*` write.** `_register_blends` populates `class_map` (`blended.py:209`) and `teacher_map` (`:211`) *before* the grade range is computed (`:213`). A `continue` placed after the grade range leaves two of three maps populated while `metadata` is skipped — `_emit_missing_blended_classes` (iterates metadata) then omits the class while `assign_class_ids` (`base.py:473`) and co-teacher **path 2** (`enrollments.py:324`) still reference it: **orphan Class IDs in `Enrollments.csv`, byte-for-byte the `e187ac8` partner-ingest rejection.** The grade set must be computed and tested between `blended.py:203` and `:205`. This is an acceptance criterion, not a note.

**`blended._load_reference_frames` is deliberately NOT touched.** Filtering the schedule there would change the per-MT-ID **mode** grade (`blended.py:329`), hence blend identity and naming, and would shrink the fallback section universe when ClassInformation lacks required columns (`:137-143`). Suppression happens at one point only.

**Alternatives rejected:** a boolean `homeroom_classes_only` (cannot express shapes 2 or 3); dropping `student_schedule` from a district's `source_files` (fails — `enrollments.py:33-35` returns empty when the schedule is empty, deleting homeroom enrollments too); suppressing classes with no student enrollments (silently changes six live districts; can't distinguish "legitimately empty" from "wrongly rostered"); pre-filtering the schedule in `blended.py` (see above).

### Advisory-panel defects and how this design answers them

| Defect found at Stage 2b | Answer |
|---|---|
| Suppression predicate keyed to `homeroom_grades` left straddling blends alive as studentless teacher-only classes | Rule is keyed to the **timetable scope** — the set that actually receives subject enrollments |
| `_register_blends` writes `class_map`/`teacher_map` before the grade range is known → orphan Class IDs | Suppression placed before any `result.*` write; acceptance criterion + two-sided pairing test |
| `grade_to_ceds` is **not idempotent** (`KG`/`PK`/`IT`/`PR`/`PS` → `"UG"`); a filter on the wrong side of the in-place rewrite (`grades.py:90`) silently deletes Kindergarten | Scope applied **inside** `split_by_homeroom_grades`, which converts once; no external filter, no ordering choice to get wrong |
| Schedule pre-filter changes blend identity/naming and the fallback universe | `_load_reference_frames` untouched |
| New `GlobalConfig` field invisible to the ETL | `to_raw_dict` (`models.py:750-767`) is a hand-enumerated allowlist — explicit affected-file entry + round-trip pin + completeness test |
| Two homeroom-side call sites provably dead | Subset rule makes them unnecessary; homeroom path unchanged |

## Architecture & holistic fit

- **Codebase fit.** Validation at the Pydantic boundary (`models.py`); the scope decision inside `grades.py`, the module that already owns the grade vocabulary and the homeroom/subject partition; one gated rule in `blended.py`. No new module, no new abstraction, no district names in code. The key joins the `global_config` opt-in family (`excluded_course_codes`, `cross_enrollment.collapse`, `course_start_grade`).
- **Product fit.** Three real district shapes, one config key, no hand-edited CSVs — per CLAUDE.md's configurable-over-hardcoded mandate.
- **Quality dimensions to uphold:**
  - `data-and-persistence` — the config→pipeline key contract (`to_raw_dict`), the versioned-schema gate, referential integrity (the `e187ac8` class of failure).
  - `reliability-resilience` — fail-loud validation (subset rule, CEDS codes) + a floor against silent filter-to-empty delivery.
  - `maintainability-structure` — one conversion, one suppression point, scope derived once.
  - `testing` — the default-off proof needs a real positive twin (CLAUDE.md's no-vacuous-greens rule).
  - *Not in scope:* `security` (no new boundary/input), `performance-efficiency` (one extra mask), privacy (no new PII surface).
- **Future-proofing.** The rule is total over the three shapes and gated for 0043; 0043 deletes the gate rather than rewriting the rule.

## Affected files

- `src/config/models.py` — `GlobalConfig.class_rostering_grades: Literal["homeroom"] | list[str] | None = None`; **add the key to `to_raw_dict`'s `global_raw` (`:750-767`)**; validators (below).
- `src/config/loader.py:63` — `SUPPORTED_CONFIG_MINOR` 9 → 10 (ETL-affecting key).
- `src/etl/transformers/grades.py` — `split_by_homeroom_grades` gains optional `timetable_scope`; a small resolver turning the config value + `homeroom_grades` into that scope (`None` ⇒ today's complement).
- `src/etl/transformers/classes.py:226` · `src/etl/transformers/enrollments.py:186` — pass the scope through.
- `src/etl/transformers/blended.py:198-220` — extract `_blend_grades`; gated suppression **before** the first `result.*` write.
- `config/mappings/sd83myedbc_mapping.yaml` — `class_rostering_grades: "homeroom"`; `version: '1.9'` → **`'1.10'` (quoted — PyYAML collapses bare `1.10` → `1.1`)**.
- Tests: `test_config.py` · `test_config_version_gate.py` · `test_contract.py` (`TestDistrictQuirks`) · `test_transform_classes.py` · `test_transform_enrollments.py` · `test_blended_classes.py` · `test_zero_orphan_enrollments.py` · a differential test over the SD74 corpus.
- Docs: `docs/claugentic-ARCHITECTURE_TREE.md` · `docs/developer/adding-district.md` · **`docs/developer/output-contract.md:244`** (the sentence stating the CURRENT rule — "homeroom classes are auto-generated for the configured `homeroom_grades`, subject classes come from the schedule" — which is partner-facing and changes when the key is set; `:483`'s gated columns do NOT move, since SD83's entity set is unchanged — only its free-text Note) · `CLAUDE.md` — one dense clause each.

**NOT in this plan — the delivery floor (owner decision, 2026-08-13).** The drafted floor ("`Students` non-empty while BOTH rostering entities are empty is a fault") **contradicts pinned, documented behaviour**: `tests/test_pipeline_delivery_integrity.py:236-237` (`test_anchor_alone_is_clean`) explicitly asserts that state CLEAN with the full rostering set configured, `:231-234` pins per-entity skip-on-empty as legitimate BY DESIGN, and `pipeline.py:279-285`/`:767-768` + CLAUDE.md's exit-code contract say the same. `active_entities()` scoping stops `mbponly`/`mbp_core`/`sd51attendance` but NOT the real false positive: any of the 8 rostering configs whose `student_schedule` is missing/empty **and** whose demographic frame yields no homeroom-grade rows hits it — one missing GDE away, since `enrollments.transform` (`enrollments.py:33-35`) returns empty for the whole entity on an empty schedule. Today that is a partial delivery at exit 0; under the floor it becomes exit **1 with nothing written at all** (the fault raises before `save_all`). It also has a second caller (`src/ui_flet/screens/convert.py:281`) requiring a new bounded `RunErrorCategory` (written into `history.db`), a new `ConvertStatus` + plain-language copy (`convert_result.py:140-184`), and an entry in the hand-listed faults at `tests/test_ui_flet_convert_result.py:199-202`. Owner dropped it here because **the validators already fail loud at config LOAD time, before any run** — the floor's only residual is "valid config, no student holds those grades", which the >20% anomaly already warns on for any district with a prior run. → ROADMAP item; `product-ux` is therefore NOT in scope for this plan.

### Validators (fail-loud, at the boundary)

**Both lists are in CEDS OUTPUT space, not raw source space** — `myedbc_mapping.yaml:9` and `sd83myedbc_mapping.yaml:31` use `IT/PR/PK/TK/KG/01…`, and the runtime compare is against the CONVERTED column (`grades.py:91,93`). Stated explicitly because an implementer could otherwise reasonably build the subset check against raw MyEd values (`"K"`, `"3"`).

1. **Subset rule** — `model_validator(mode="after")`: when the list form is used, `set(homeroom_grades) ⊆ set(class_rostering_grades)`, else raise naming both sets.
2. **Sentinel guard** — `"homeroom"` with an EMPTY `homeroom_grades` means "roster nobody" → raise. **Scoped to the sentinel only**: the list form legitimately allows `homeroom_grades: []` (shape 2).
3. **Shape** — non-empty list; entries must be CEDS codes (message derives the valid set from `grades.CEDS_MAPPING.values()`, never restated); reject bare strings that look like a grade (`"09"`), wrong types, `""`; decide + pin case handling for `"HOMEROOM"`.
4. **`homeroom_grades` CEDS validation whenever the key is set** (Stage-3 gate, R5). The subset rule only mitigates an unvalidated `homeroom_grades` in the **list** form (a non-CEDS homeroom entry then either appears in `class_rostering_grades` — caught by validator 3 — or fails the subset check). Under the **sentinel**, rostered ≡ `homeroom_grades` and **nothing validates it** — which is exactly SD83's shipped shape. Validate `homeroom_grades` against the CEDS value set whenever `class_rostering_grades` is set; the machinery is already there. (General `homeroom_grades` validation stays a roadmap item.)
5. **`keep="homeroom"` + `timetable_scope` must RAISE**, never silently ignore the argument — CLAUDE.md's "no permissive default on a safety-relevant parameter"; a filter argument accepted and dropped is exactly the defaulted-unsafe-call that rule bans.

## Research / grounding

- **Files reviewed:** `classes.py:30-67,87-97,102-185,202-244,249-291` · `enrollments.py:27-73,93-159,164-223,229-246,251-349` · `blended.py:57-93,98-147,184-225,259-282,320-332` · `grades.py:17-93` · `models.py:466-519,750-767` · `loader.py:43-63,98-113` · `pipeline.py:211-220,249,296-315,373-388` · `loader.py:362-399` · `quality/report.py:94-156` · `contract_schema.py:132-145` · `test_contract.py:122-140,183-216,653,736,803-819`.
- **Harness docs consulted:** `docs/claugentic-WORKFLOW.md` · `CLAUDE.md` · `docs/developer/output-contract.md` · `docs/claugentic-DECISIONS.md` · the four Stage-2b lens modules.
- **Findings (verified by reading or execution, not assumed):**
  - `to_raw_dict` hand-enumerates 14 `global_config` keys — confirmed, `models.py:750-767`.
  - `grade_to_ceds("KG") == "UG"` — confirmed by execution; same for `PK`/`IT`/`PR`/`PS`.
  - `_register_blends` map-write ordering — confirmed, `blended.py:205-220`.
  - `SUPPORTED_CONFIG_MINOR = 9`; SD83 at `version: '1.9'` — confirmed.
  - `check_delivery_integrity` guards only the `Students` anchor and "no output at all" — empty Classes+Enrollments passes today.
  - `split_by_homeroom_grades(keep="subject")` derives a NEW `grade_ceds` column and preserves raw `grade` (`grades.py:92-93`) — which is why the scope belongs inside it.
  - **Gotcha (out of scope, roadmapped):** `blended.create_name` raises `KeyError: 'course code'` when ClassInformation lacks that column (`blended.py:303`).

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| **Key ships inert** — added to `GlobalConfig` but not `to_raw_dict` | Explicit affected-file entry + round-trip pin (precedent `test_config.py:515`) + a completeness test over `GlobalConfig.model_fields`. |
| **Older exe rosters everything** — `MappingConfig` is `extra="ignore"` | Bump `SUPPORTED_CONFIG_MINOR` **and** the config `version` (quoted `'1.10'`); move `loader.py:44`'s "which declare 1.0–1.9 today" prose in lockstep. **Honest residual:** the bump does NOT prevent this — a pre-bump exe reading SD83 at `'1.10'` logs a WARNING and then **RUNS** (`loader.py:142-150`), rostering 9-12 anyway. It announces the mismatch; it does not stop it. |
| **Typo'd key silently rosters everyone** — `GlobalConfig` does not forbid extras | Positive pin asserting the key's presence in the shipped SD83 config. |
| **Orphan regression (`e187ac8`)** | Suppression before any `result.*` write; two-sided pairing assertion (blend absent from Classes **and** Enrollments, asserted together). |
| **Silent non-delivery** — a config that empties both rostering entities passes the anchor-only integrity check; previous good files are archived out of the SFTP glob; the run reports success | **Accepted for this plan, mitigated upstream:** all five validators fail LOUD at config load, before any run — so the residual is only "valid config, no student holds those grades", which the >20% anomaly warns on for any district with a prior run. The floor that would close the first-run case is deferred (see Affected files) because it contradicts a pinned contract. |
| **`UG` cohort de-rostered** — a blank/unrecognised grade maps to `"UG"`; today it lands in the subject half and gets a class, under the list form it gets nothing unless `"UG"` is listed | **Accepted and documented**, not silent: log the count; name it in the DECISIONS line, the SD83 rollout note, and `adding-district.md` (a district wanting them must list `"UG"`). |
| **First run trips the >20% anomaly** on Classes + Enrollments; Home paints it a warning | Expected degradation — call it out in the rollout note so nobody "fixes" it. |
| **Partner acceptance unknown** — `Students.csv` will carry a whole 9-12 cohort with **zero** enrollment rows | **Owner/partner confirmation item.** Internal paths verified clean (`report.py:94-156` has no students→enrollments direction; the anchor rule holds), but SpacesEDU has rejected this repo's output before. Raise before SD83's first live delivery. |
| SD83 config unverified against real GDE extracts | Pre-existing for the whole SD83 config; flagged in DECISIONS. |

## Test strategy

**The negative, stated correctly:** the **11** configs that do not set the key are byte-identical — SD83 *is* one of the 12 and is *supposed* to change. The contract sweep asserts **shape only** (entity set, column order, BOM), so the single real content oracle is `tests/test_regression_sd74.py` (156 Classes rows, grades KG-12 + 5 blends). A red `EXPECTED_ENTITIES` row for sd83 is a **design error, never an edit**.

**Positive twin — pinned by value, on the corpus that matters:**
- **Differential over the SD74 corpus** (precedent `tests/test_pipeline_parity.py:267-277`): run `tests/snapshots/input/` twice, once with the sentinel injected. Assert exact set deltas — removed Class IDs are exactly the non-homeroom-grade + `BLENDED_` ids; `classes_on ⊂ classes_off`; every removed Class ID also removed from Enrollments; **`Students.csv` `assert_frame_equal`-identical**. Differential, so it survives 0043's golden regeneration.
- **SD83's contract output pinned by value** in `TestDistrictQuirks`, reusing the module-scoped run at zero extra cost. Fixture grades are 3/10/12: Class IDs == exactly the grade-3 homeroom (set equality, not a count); no `MT002_`/`MT003_`; no `BLENDED_`; `Students.csv` User IDs still `{S001,S002,S003}`; `StudentCourses.csv` non-empty containing S002/S003 (the non-goal, proven).
- **NOT** a new SD83 case in `test_pipeline_e2e_districts.py` — the 12-config sweep already runs sd83 end-to-end, and that module writes a 5-entity input set.

**All three shapes exercised** (the semantics are the feature):
- Shape 1 (`"homeroom"`): homerooms only; no subject class; no blend.
- Shape 2 (`homeroom_grades: []`, list `["10","11","12"]`): **no** homeroom classes; subject classes for 10-12 only; a 10/11 blend survives.
- Shape 3 (homeroom 07-09, list 07-12): (i) homerooms 7-9; (ii) subject 10-12; K-6 absent from both; (iii) a **09/10 blend survives carrying its grade-10 students**; a 07/08 blend suppressed; **(iv) the mode-masking fork** — a section whose MODE grade is out of scope but which carries minority in-scope students: assert the blend is suppressed AND those students appear in the plain per-section class, with **no orphan** (the R3(i) named behaviour).

**Boundary + wiring:** `to_raw_dict` round-trip + completeness test · subset-rule violation raises · sentinel + empty `homeroom_grades` raises **while list + empty `homeroom_grades` is accepted** (shape 2) · non-CEDS entries raise · **non-CEDS `homeroom_grades` raises when the key is set** (validator 4 — the sentinel's only cover) · **`keep="homeroom"` + `timetable_scope` raises** (validator 5) · version gate pinned · `_base` inheritance (child inherits; child overrides to `null` — pin whichever deep-merge gives).

**Integrity:**
- Two-sided pairing: a suppressed blend is absent from Classes **and** Enrollments, asserted together (`enrolled ⊆ classes` alone is satisfiable by emptiness).
- Populated ClassInformation with `Primary Teacher = Y`: co-teacher **path 1** still emits K-8 homeroom teacher rows; **path 2** emits nothing for a suppressed blend. (The sweep is structurally blind here — SD83's fixture writes an empty ClassInformation.)
- Homeroom classes for `["K","1","3"]` all created (guards the CEDS-idempotency class of bug).
- **Flag OFF, gate pinned:** an all-homeroom blend is still present in `class_map`/`metadata`/`teacher_map`. The only proof suppression doesn't fire on the six live districts — and **0043 deletes the golden that currently implies it**, so it must exist as a unit test now.
- `_blend_grades` consumed by all three of `validate` / `get_grade_range` / the suppression check (no fourth spelling).

## Decomposition (slices)

- [ ] **Slice 1** — the feature: config key + 5 validators + `to_raw_dict` wiring, version-gate bump (+ `loader.py:44` prose), scope resolver + `split_by_homeroom_grades` change, two call sites, `_blend_grades` extraction + gated blend suppression, SD83 config, tests, docs.

**Why one slice:** splitting config from wiring lands a validated-but-inert key — the exact half-done state the workflow forbids, and the panel's highest-severity finding. With the delivery floor removed (owner, 2026-08-13) this is ~13 files, three touch points, one concept — the Stage-3 gate confirmed it "lands vertically complete without the floor, which guards a mis-configuration rather than the feature's vertical", and judged it not gold-plated: one key, one resolver, one masked filter, one gated `continue`.

---

## Review  _(Stage 3 — plan-gate)_

- **Verdict:** **CHANGES REQUIRED** (7 required changes + a split; the core design is sound)

**Independently verified against the code — these load-bearing claims HOLD, do not re-open them:**
- **The subset-rule simplification (item 1) is CORRECT.** Traced `_create_homeroom_classes` (`classes.py:102-185`) and `_homeroom_enrollments` (`enrollments.py:93-159`): the homeroom side is a pure grade-membership test on the demographic frame, the homeroom Class ID is `{school}_{homeroom}_{year}` (`classes.py:157-163` — no grade term), the enrollment merge key is `[school_number, homeroom]` (`enrollments.py:125`), and co-teacher path 1 matches by section letter (`enrollments.py:296-321`). **No path makes a homeroom-grade student's rostering depend on a grade outside `homeroom_grades`.** The "two dead call sites → unnecessary" disposition is right.
- **The suppression insertion point (item 3) is CORRECT.** After the `validate` continue (`blended.py:202-203`), before `blended_id` (`:205`) and the `class_map`/`teacher_map` writes (`:209`/`:211`). Nothing earlier in `_register_blends` mutates shared state — `_teacher_positions` (`:194`) is read-only and `result` is a fresh `BlendedDetection.empty()` (`:195`).
- **Putting the scope inside `split_by_homeroom_grades` (item 4) genuinely kills the ordering hazard.** `keep="subject"` derives `grade_ceds` once from the raw column (`grades.py:92`) and the scope mask applies to that; `keep="homeroom"`'s in-place rewrite (`:90`) is untouched and never followed by a second conversion. Both callers are safe — `get_source_file` returns a `.copy()` (`sources.py:41-48`) and `enrollments.py:176` copies again, so no cross-transformer double conversion exists today either.
- **Version bump (item 6) is the right call** — `class_rostering_grades` is ETL-affecting per the 2026-07-29 scope note (`loader.py:49-61`), and the gate's tests reference the constants symbolically (`test_config_version_gate.py:79,103,125,135`), so the bump is mechanically safe. `_parse_version`'s regex handles `'1.10'`. No other config's declared version moves (`sd40/54/60/mbp_* = '1.0'`; rest `'1.9'`).
- **Test strategy is otherwise strong**: the differential-over-SD74 has a working precedent (`tests/test_pipeline_parity.py:255-290` runs one corpus twice), the negative is stated correctly (11, not 12), and the flag-OFF gate pin is the right call given 0043 deletes the golden that currently implies it. `to_raw_dict` round-trip precedent confirmed at `test_config.py:515`.

### Required changes

1. **Resolve the delivery floor's blast radius — it currently contradicts pinned, documented behaviour.** `tests/test_pipeline_delivery_integrity.py:236-237` (`test_anchor_alone_is_clean`) explicitly pins *Students-alone with the full rostering set configured* as CLEAN, `:231-234` pins per-entity skip-on-empty as legitimate BY DESIGN, and `pipeline.py:279-285` + `:767-768` + CLAUDE.md's exit-code contract all say the same. The proposed floor turns that into exit **1** with **nothing written at all** (`pipeline.py:761-763` raises *before* `save_all`). The `active_entities()` scoping handles `mbponly`/`mbp_core`/`sd51attendance` but does **not** stop the real false positive: any of the 8 rostering configs whose `student_schedule` is missing/empty **and** whose demographic frame yields no homeroom-grade rows produces `Classes={}` + `Enrollments={}` with Students non-empty. That is one missing GDE away, not exotic — `enrollments.transform` (`enrollments.py:33-35`) returns empty for the **whole entity** when the schedule is empty. Pick one and write it into the plan: **(a) gate the floor on `class_rostering_grades` being set** (matches the plan's own gating principle for the blend rule and keeps the blast radius at the new feature — note this requires the gate to see `global_config`, i.e. a signature change at BOTH call sites), or **(b) keep it unconditional and OWN the contract change** — exit-code contract in CLAUDE.md + `docs/developer/output-contract.md`, rewrite `test_anchor_alone_is_clean`, and surface it as an explicit Stage-5 trade-off. Silence is not an option; today the plan reads as if `active_entities()` scoping were sufficient.
2. **Add the floor's second call site and its UI surface to Affected files.** `check_delivery_integrity` has TWO callers: `pipeline.py:761` **and** `src/ui_flet/screens/convert.py:281`. A new fault needs: a new `RunErrorCategory` member (`pipeline.py:70-83` — a bounded closed set written into `history.db`), a new `ConvertStatus` **with plain-language copy** in `src/ui_flet/convert_result.py:140-184`, the `_INTEGRITY_FAULT_STATUSES` table entry (`:181-184`), and an added entry in the **hand-listed** faults of `tests/test_ui_flet_convert_result.py:199-202` — that "every category is mapped" test is only as total as its hand-written call list and will **not** catch the omission by itself. Reusing `INCOMPLETE_ROSTER` is not available: its copy is *"Your student list came through empty"*, which would be false here. This also puts **`product-ux`** in scope — add it to the quality-dimension list.
3. **Restate the blend rule as necessary-but-not-sufficient, and test the mode-masking fork.** `_build_grade_map` (`blended.py:320-332`) keys each MT ID to its **MODE** grade, so `blend_grades` is a set of *mode* grades. (i) A section whose mode grade is out of the timetable scope but which carries minority in-scope students → the blend is **suppressed**; those students are *not* orphaned (suppression precedes the `class_map` write, so `assign_class_ids` resolves the plain MT-ID class consistently in both `classes.py:231` and `enrollments.py:190` — verified), but they land in a per-section class instead of the blend. Name that fork and add it to the shape-3 cases. (ii) A **surviving** blend can still be studentless (its in-scope students all inactive → dropped by `filter_to_active`), so drop "definitionally no students" and assign that residual to 0043. (iii) State and pin the favourable fact the plan omits: `validate()` (`blended.py:259-269`) already requires ≥2 resolvable CEDS grades, so a blend reaching the check can never carry an EMPTY grade set — the rule can never suppress on "grades unknown".
4. **Single-source the blend grade-set derivation.** `validate` (`:264-269`) and `get_grade_range` (`:271-282`) already duplicate the MT-ID→CEDS grade-set loop; the suppression check makes three. Specify one helper (e.g. `_blend_grades(group, mtid_to_grade) -> set[str]`) consumed by all three, or Stage 7 will flag the DRY violation.
5. **Fix the CEDS-vocabulary mitigation claim, and state the grade space.** The subset rule only mitigates unvalidated `homeroom_grades` in the **list** form (a non-CEDS homeroom entry then either appears in `class_rostering_grades` — rejected by the CEDS validator — or fails the subset check). With the `"homeroom"` sentinel, rostered ≡ homeroom_grades and **nothing validates it** — which is exactly SD83's shipped shape. Either validate `homeroom_grades` against the CEDS value set whenever the key is set (cheap; the machinery is already there) or narrow the Non-goals claim. Separately, state explicitly that **both lists are in CEDS output space** (`myedbc_mapping.yaml:9` / `sd83myedbc_mapping.yaml:31` use `IT/PR/PK/TK/KG/01…`, and the runtime compare is against the converted column — `grades.py:91,93`); otherwise an implementer may reasonably build the subset check against raw source values.
6. **Version-gate lockstep + honest residual.** (a) `loader.py:44`'s "which declare 1.0–1.9 today" must move with the constant. (b) State the residual plainly: a pre-bump exe reading SD83 at `'1.10'` logs a WARNING and then **runs**, rostering 9-12 anyway (`loader.py:142-150`) — the current risk row reads as if the bump prevented that; it only announces it. (c) Decide + pin the `keep="homeroom"` + `timetable_scope` combination in `split_by_homeroom_grades` — per CLAUDE.md's "no permissive default on a safety-relevant parameter", **raise**; silently ignoring a filter argument is exactly the defaulted-unsafe-call the rule bans.
7. **Complete the doc list.** `docs/developer/output-contract.md:244` — not `:483` — is the sentence that *states the current rule* ("homeroom classes are auto-generated for the configured `homeroom_grades`, subject classes come from the schedule"); it is partner-facing and changes when the key is set. (`:483`'s gated columns don't move, since SD83's entity set is unchanged — only its free-text Note.) Also: `docs/claugentic-DECISIONS.md:7` (2026-08-13) prescribes the fix as *"a new opt-in `global_config` flag that pre-filters `student_schedule`"* — which this plan deliberately rejects, with good reasons (`blended.py:329` mode/naming + the `:137-143` fallback universe). Land must append a superseding line, or a future agent re-litigates it.

### Sizing / completeness check

- **Slice 1 (as written) — SPLIT NEEDED.** It is ~20 files spanning config → three transformers → pipeline → **UI copy + run-store taxonomy** → 8-10 test modules → 4 docs, and it bundles an opt-in ETL feature with a cross-district delivery-**severity** change that carries its own Stage-5 trade-off. Split:
  - **Slice 1a — the feature (vertically complete).** `GlobalConfig` field + validators + `to_raw_dict`; `SUPPORTED_CONFIG_MINOR` bump + SD83 `'1.10'`; the scope resolver + `split_by_homeroom_grades`; the two call sites; gated blend suppression; all ETL tests (three shapes, flag-OFF gate pin, two-sided pairing, SD74 differential, `TestDistrictQuirks`); docs. **Lands complete** — the feature works end to end without the floor, which guards a mis-configuration rather than the feature's vertical.
  - **Slice 1b — the delivery floor.** The change-1 decision plus the change-2 surface (new category, Convert copy, both call sites, run-store value, tests). Legitimate alternatives: defer to a roadmap item, or fold into 0043 with the ungating. Do **not** land it as a bullet inside 1a.
- After that split both slices are comfortably session-sized. **YAGNI:** with 1b removed, 1a is *not* gold-plated — one key, one resolver, one masked filter, one gated `continue`. The `"homeroom"` sentinel is defensible sugar (it makes the two keys structurally undriftable for the one shipped consumer); keep it.
- **Right path (Stage 0):** full pipeline confirmed correct — shared-contract change (config schema + output semantics + version gate), 8+ files, partner-visible output.

### Harness impact

- **DECISIONS:** two dated lines at Land — (i) the owner's 2026-08-13 semantics (`class_rostering_grades` = the COMPLETE rostered set; `homeroom_grades` ⊆ it; timetable scope = the difference), (ii) a line **superseding** `DECISIONS.md:7`'s prescribed schedule-pre-filter fix, naming why (blend identity/naming via the per-MT-ID mode grade + the ClassInformation fallback universe).
- **CLAUDE.md:** one dense clause under *Configuration-Driven Design* for the key + the new `SUPPORTED_CONFIG_MINOR`. If required change 1 lands as option (b), the **exit-code contract** line changes too.
- **ROADMAP:** update the 0043 entry — it currently states the rule as `grades ⊆ homeroom_grades`, which is **not** the rule this plan implements (they coincide only under the key-absent default). Leaving it will mislead 0043's implementer. Add the R3 residuals (inactive-only and mode-masked studentless blends) there.
- **No new STANDARD or agent required.** One `docs/claugentic-standards/CANDIDATES.md` candidate if the panel agrees: *a hand-listed "totality" test is only as total as its call list* (`tests/test_ui_flet_convert_result.py:190-205`) — the no-vacuous-greens family.

### Disposition of the 7 required changes (applied 2026-08-13)

| # | Required change | Disposition |
|---|---|---|
| 1 | Delivery floor contradicts pinned behaviour — gate it, or own the contract change | **Owner dropped it from this plan** (option c, not offered by the gate but the honest third door: the validators already fail loud at LOAD time, so the floor's only residual is a first-run data condition). → ROADMAP. `product-ux` removed from scope. |
| 2 | Floor's 2nd call site + UI surface missing from Affected files | **Moot** — floor removed. Documented in the NOT-in-this-plan block so 0043/the roadmap item inherits the full surface list. |
| 3 | Blend rule over-claimed; mode-masking fork; `validate()` ≥2-grades fact | **Applied** — restated as necessary-not-sufficient with (i) mode-masking named + tested as shape-3 case (iv), (ii) inactive-only residual assigned to 0043, (iii) the ≥2-grades fact pinned. |
| 4 | Single-source the blend grade-set derivation | **Applied** — `_blend_grades(group, mtid_to_grade)` extracted, consumed by all three sites, with a test that no fourth spelling appears. |
| 5 | CEDS mitigation doesn't hold for the sentinel; state the grade space | **Applied** — validator 4 (validate `homeroom_grades` whenever the key is set) + an explicit statement that both lists are CEDS OUTPUT space. |
| 6 | Version-gate lockstep + honest residual + `keep="homeroom"` guard | **Applied** — `loader.py:44` prose added; the risk row now says plainly the bump **announces** rather than prevents; validator 5 raises on the ignored-argument combination. |
| 7 | Doc list: `output-contract.md:244` not `:483`; supersede `DECISIONS.md:7` | **Applied** — doc line corrected with the reason; the superseding DECISIONS line is in Harness impact. |

### Stage 2b advisory panel (complete — advisory, contributed not gated)
| Lens | Verdict | Disposition |
|---|---|---|
| `yagni-sentinel` | OVER-BUILT (~3×) | **Partly accepted** — dead call sites gone, duplicate e2e cut, no schedule pre-filter. Its central cut (drop the list form) is **overridden by the owner**, who supplied the two concrete shapes and the subset semantics whose absence was the agent's strongest argument. |
| `maintainability-structure` | GAPS | **Accepted** — `to_raw_dict`; scope resolved once and applied inside the one converting function (its `GradeScope` recommendation, in the smallest form that works). |
| `data-and-persistence` + `reliability-resilience` | GAPS | **Accepted** — F1 predicate now keyed to timetable scope, F2 map-ordering an acceptance criterion, F3 `to_raw_dict`, F4 version gate, F5 idempotency avoided structurally, F6 delivery floor (scoped), F7 UG cohort, F8 no pre-filter, F9 partner question. |
| `testing` | GAPS | **Accepted** — negative restated, differential twin, `TestDistrictQuirks` placement, flag-OFF gate pin, round-trip, two-sided pairing; plus per-shape cases for the three semantics. |

---

## Spec  _(Stage 4)_

### Slice 1 — `class_rostering_grades`

**In plain English (shown first at the approval gate):**

- **What this builds.** One new setting in a district's mapping file that says *which grades get class rosters at all*. Districts that don't set it are completely unaffected. SD83 sets it to `"homeroom"` and gets K-8 homeroom classes only — no grade 9-12 timetable classes, no duplicate blended classes — while grades 9-12 stay on the student list so their myBlueprint+ transcripts still work. It also covers two shapes you named for later: "no homerooms, timetable rostering for grades 10-12 only", and "homerooms for 7-9, timetable for 10-12, K-6 excluded".
- **What "done" means for you.** SD83 converts and produces `Classes.csv` containing only its K-8 homerooms; the other 11 configs produce byte-identical output to today, proven by the frozen SD74 regression; a mis-typed setting is rejected when the config loads, with a message naming what's wrong.
- **What you're accepting.**
  1. **`Students.csv` will contain grades 9-12 with no class enrolments at all.** Internally clean (verified), but **not yet confirmed with SpacesEDU** — worth asking before SD83's first live delivery.
  2. **SD83's first run after this lands will show a warning**, because Classes and Enrollments drop >20% versus the previous run. Expected, not a fault.
  3. **Students with a blank or unrecognised grade** (mapped to `UG`) currently get a timetable class; under this setting they get nothing unless `"UG"` is listed. Logged, documented, not silent.
  4. **An older DistrictSync exe reading the new SD83 config will warn and then run anyway**, rostering 9-12. The version bump announces the mismatch; it does not prevent it.
  5. **No safety floor against a valid-but-empty grade list.** Deferred deliberately — it contradicts a pinned contract and would turn a missing schedule file into a failed run for every district.

**Files & changes**

| File | Change |
|---|---|
| `src/config/models.py` | `GlobalConfig.class_rostering_grades: Literal["homeroom"] \| list[str] \| None = None`; 4 validators (subset · sentinel-vs-empty-homeroom · CEDS shape · `homeroom_grades` CEDS when key set); **add the key to `to_raw_dict`'s `global_raw` (`:750-767`)** |
| `src/config/loader.py` | `SUPPORTED_CONFIG_MINOR` 9 → 10; `:44` prose in lockstep |
| `src/etl/transformers/grades.py` | `resolve_timetable_scope(global_config, homeroom_grades) -> set[str] \| None`; `split_by_homeroom_grades(..., timetable_scope=None)` — `keep="subject"` masks on it; `keep="homeroom"` **raises** if passed one |
| `src/etl/transformers/classes.py:226` · `enrollments.py:186` | pass the scope through |
| `src/etl/transformers/blended.py` | extract `_blend_grades(group, mtid_to_grade) -> set[str]`; consume from `validate` + `get_grade_range`; gated suppression **between `:203` and `:205`** |
| `config/mappings/sd83myedbc_mapping.yaml` | `class_rostering_grades: "homeroom"`; `version: '1.10'` (quoted) |

**In-scope standards dimensions:** `data-and-persistence` (config→pipeline key contract, versioned schema, referential integrity) · `reliability-resilience` (fail-loud validation, no silent filter-to-empty) · `maintainability-structure` (one conversion, one suppression point, no fourth grade-set spelling) · `testing` (no vacuous greens). **Out of scope:** `security`, `performance-efficiency`, `product-ux`, privacy.

**Tests to add:** as enumerated in Test strategy — the SD74 differential twin, `TestDistrictQuirks` value pins, all three shapes incl. the mode-masking fork, the flag-OFF gate pin, two-sided blend pairing, co-teacher path 1 vs path 2, `to_raw_dict` round-trip + completeness, the 5 validators, version-gate pin, `_base` inheritance.

**Acceptance criteria**

1. `sd83myedbc` emits `Classes.csv` with **only** homeroom Class IDs — no `BLENDED_`, no per-MT subject IDs — and `Students.csv`/`StudentCourses.csv` unchanged.
2. The other **11** configs are byte-identical; `tests/test_regression_sd74.py` passes **untouched**.
3. **Blend suppression occurs before any `result.*` write** in `_register_blends`; a suppressed blend appears in **neither** `Classes.csv` nor `Enrollments.csv`, asserted together.
4. Every one of the 5 validators raises with an actionable message; `homeroom_grades: []` is accepted with the list form and rejected with the sentinel.
5. `to_raw_dict` carries the key (round-trip pinned) and the completeness test covers every non-presentation `GlobalConfig` field.
6. All gates green: full suite, SD74 snapshot, tree-check, ruff, mypy, bandit, email scan, 12/12 configs.
7. No new tech debt; ARCHITECTURE_TREE + DECISIONS + ROADMAP updated at Land.
