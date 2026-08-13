# 0042 — `class_rostering_grades`: opt-in grade scoping for class rostering

- **Status:** In Review (revised twice — after the Stage-2b advisory panel, then after the owner's 2026-08-13 semantics decision)
- **Resumable from:** Stage 3 plan-gate
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
3. Config + wiring: the field, its validators, `to_raw_dict`, the version gate, the delivery floor.

**Blend-rule derivation, checked against all three shapes.** The rule is keyed to the timetable scope because that is *precisely* the set whose students receive subject enrollments — so "no grade in timetable scope" is definitionally "no students".

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
- `src/etl/transformers/blended.py:198-220` — gated suppression **before** the first `result.*` write.
- `src/etl/pipeline.py` — delivery floor: `Students` non-empty while BOTH rostering entities are empty is a fault, not a warning. **Must not false-positive on `mbponly`/`mbp_core`/`sd51attendance`, which legitimately enable no Classes/Enrollments — scope the floor to configs whose `active_entities()` INCLUDE them.**
- `config/mappings/sd83myedbc_mapping.yaml` — `class_rostering_grades: "homeroom"`; `version: '1.9'` → **`'1.10'` (quoted — PyYAML collapses bare `1.10` → `1.1`)**.
- Tests: `test_config.py` · `test_config_version_gate.py` · `test_contract.py` (`TestDistrictQuirks`) · `test_transform_classes.py` · `test_transform_enrollments.py` · `test_blended_classes.py` · `test_zero_orphan_enrollments.py` · a differential test over the SD74 corpus.
- Docs: `docs/claugentic-ARCHITECTURE_TREE.md` · `docs/developer/adding-district.md` · `docs/developer/output-contract.md:483` · `CLAUDE.md` — one dense clause each.

### Validators (fail-loud, at the boundary)

1. **Subset rule** — `model_validator(mode="after")`: when the list form is used, `set(homeroom_grades) ⊆ set(class_rostering_grades)`, else raise naming both sets.
2. **Sentinel guard** — `"homeroom"` with an EMPTY `homeroom_grades` means "roster nobody" → raise. **Scoped to the sentinel only**: the list form legitimately allows `homeroom_grades: []` (shape 2).
3. **Shape** — non-empty list; entries must be CEDS codes (message derives the valid set from `grades.CEDS_MAPPING.values()`, never restated); reject bare strings that look like a grade (`"09"`), wrong types, `""`; decide + pin case handling for `"HOMEROOM"`.

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
| **Older exe silently rosters everything** — `MappingConfig` is `extra="ignore"` | Bump `SUPPORTED_CONFIG_MINOR` **and** the config `version` (quoted). Pin SD83's declared version ≥ the introducing minor. |
| **Typo'd key silently rosters everyone** — `GlobalConfig` does not forbid extras | Positive pin asserting the key's presence in the shipped SD83 config. |
| **Orphan regression (`e187ac8`)** | Suppression before any `result.*` write; two-sided pairing assertion (blend absent from Classes **and** Enrollments, asserted together). |
| **Silent non-delivery** — empty Classes+Enrollments passes the anchor-only integrity check; previous good files archived out of the SFTP glob; run reports success | Scoped delivery floor (see Affected files). Must not fire for configs that enable no rostering entities. |
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
- Shape 3 (homeroom 07-09, list 07-12): homerooms 7-9; subject 10-12; K-6 absent from both; a **09/10 blend survives carrying its grade-10 students**; a 07/08 blend suppressed.

**Boundary + wiring:** `to_raw_dict` round-trip + completeness test · subset-rule violation raises · sentinel + empty `homeroom_grades` raises **while list + empty `homeroom_grades` is accepted** (shape 2) · non-CEDS entries raise · version gate pinned · `_base` inheritance (child inherits; child overrides to `null` — pin whichever deep-merge gives).

**Integrity:**
- Two-sided pairing: a suppressed blend is absent from Classes **and** Enrollments, asserted together (`enrolled ⊆ classes` alone is satisfiable by emptiness).
- Populated ClassInformation with `Primary Teacher = Y`: co-teacher **path 1** still emits K-8 homeroom teacher rows; **path 2** emits nothing for a suppressed blend. (The sweep is structurally blind here — SD83's fixture writes an empty ClassInformation.)
- Homeroom classes for `["K","1","3"]` all created (guards the CEDS-idempotency class of bug).
- **Flag OFF, gate pinned:** an all-homeroom blend is still present in `class_map`/`metadata`/`teacher_map`. The only proof suppression doesn't fire on the six live districts — and **0043 deletes the golden that currently implies it**, so it must exist as a unit test now.
- Delivery floor fires on empty rostering; does **not** fire for `mbponly`/`mbp_core`/`sd51attendance`.

## Decomposition (slices)

- [ ] **Slice 1** — the whole feature: config key + validators + `to_raw_dict` wiring, version-gate bump, scope resolver + `split_by_homeroom_grades` change, two call sites, gated blend suppression, pipeline delivery floor, SD83 config, tests, docs.

**Why one slice:** splitting config from wiring lands a validated-but-inert key — the exact half-done state the workflow forbids, and the panel's highest-severity finding. ~13 files, three touch points, one concept.

---

## Review  _(Stage 3 — plan-gate)_
- **Verdict:** _pending (re-run after the 2026-08-13 semantics revision)_

### Stage 2b advisory panel (complete — advisory, contributed not gated)
| Lens | Verdict | Disposition |
|---|---|---|
| `yagni-sentinel` | OVER-BUILT (~3×) | **Partly accepted** — dead call sites gone, duplicate e2e cut, no schedule pre-filter. Its central cut (drop the list form) is **overridden by the owner**, who supplied the two concrete shapes and the subset semantics whose absence was the agent's strongest argument. |
| `maintainability-structure` | GAPS | **Accepted** — `to_raw_dict`; scope resolved once and applied inside the one converting function (its `GradeScope` recommendation, in the smallest form that works). |
| `data-and-persistence` + `reliability-resilience` | GAPS | **Accepted** — F1 predicate now keyed to timetable scope, F2 map-ordering an acceptance criterion, F3 `to_raw_dict`, F4 version gate, F5 idempotency avoided structurally, F6 delivery floor (scoped), F7 UG cohort, F8 no pre-filter, F9 partner question. |
| `testing` | GAPS | **Accepted** — negative restated, differential twin, `TestDistrictQuirks` placement, flag-OFF gate pin, round-trip, two-sided pairing; plus per-shape cases for the three semantics. |

---

## Spec  _(per slice, after Review passes — Stage 4)_
_Pending Stage-3 gate._
