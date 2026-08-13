# 0042 — `class_rostering_grades`: opt-in homeroom-only class rostering

- **Status:** In Review (revised after Stage-2b advisory panel)
- **Resumable from:** Stage 3 plan-gate
- **Blockers:** none
- **Flags:** none
- **Disposition at close:** single slice — done or deferred per the workflow's lifecycle rule.
- **Roadmap item:** `docs/claugentic-ROADMAP.md` → Deferred tech work (0043 studentless-blend fix + the blended naming crash).
- **References:** `docs/claugentic-ARCHITECTURE_TREE.md` · `docs/claugentic-DECISIONS.md` (2026-08-13) · `docs/developer/output-contract.md` · commit `e187ac8` (the orphan-BLENDED fix this plan must not regress)

## Problem

SD83 (North Okanagan-Shuswap) runs SpacesEDU for K-8 only, but feeds myBlueprint+ course data for grades 9-12. There is no way to express that today: `homeroom_grades` decides which grades get *homeroom* classes, and every remaining grade automatically gets *subject* classes and enrollments (`classes.py:226`, `enrollments.py:186` — `split_by_homeroom_grades(..., keep="subject")` returns exactly the non-homeroom rows). So SD83's grade 9-12 timetable lands in `Classes.csv`/`Enrollments.csv` regardless.

A third path compounds it: **blended detection never applies any homeroom-grade filter** (`blended.py:57-93`). Because K-8 students only ever receive homeroom enrollments while `_blended_teacher_enrollments` (`enrollments.py:229`) filters nothing, a blend whose grades are all homeroom grades yields a class with a teacher and **zero students**.

**Evidence and its limits — read this before citing it.** The mechanism above is confirmed two ways: a purpose-built probe (a 3/4 split sharing one Homeroom value produced a correct 2-student homeroom class *plus* a studentless `BLENDED_` duplicate), and the SD74 snapshot output. **The SD74 snapshot is fabricated, not de-identified production data** — `tests/snapshots/generate_synthetic.py` ("realistic-looking but entirely fake"), `random.seed(74)`, 100 students / 10 staff / 50 sections. Its session components have almost no variety (`term` has ONE distinct value; `semester`/`day` two; `period` five), so 6 of 41 sessions collide by chance and its "blends" (e.g. `Homeroom 1 / Music 10`, 1 student) are seed artifacts, not real classes. It is therefore **valid evidence of deterministic code behaviour and of nothing else** — it says nothing about how often studentless blends occur in production. The only genuine production data point is `e187ac8`: SD40 FY2026 had 411 blended classes and 195 orphans, with no studentless breakdown available.

## Goals / Non-goals

- **Goal:** an opt-in `global_config.class_rostering_grades: "homeroom"` that restricts class rostering to homeroom classes, so SD83 emits K-8 homerooms only.
- **Goal:** absent key ⇒ **byte-identical output for the other 11 configs**. The SD74 golden must not move.
- **Non-goal:** changing `Students.csv`. Grades 9-12 MUST stay rostered — `StudentCourses` applies the zero-orphan filter against the active roster, so dropping them empties the myBlueprint+ transcripts SD83 asked for.
- **Non-goal:** filtering `Staff.csv` (owner decision — staff stay unfiltered, as in every other config).
- **Non-goal:** the **explicit grade-list form** (`class_rostering_grades: ["09","10"]`). Owner decision 2026-08-13 after the advisory panel: the key NAME is reserved so the list form can be added later without a rename or deprecation, but the list branch is **not built now** — its straddling-blend semantics are undefined and would need their own decision, slice and tests regardless. Roadmap line, not a stub.
- **Non-goal:** the general studentless-blend fix for the other six districts — partner-visible, rewrites the SD74 golden, **plan 0043**.
- **Non-goal:** validating `homeroom_grades` against the CEDS vocabulary generally (pre-existing gap → roadmap). Partially mitigated here: see the cross-field validator.

## Approach

`class_rostering_grades` accepts `"homeroom"` or is absent. **Because the sentinel makes the rostered set identical to `homeroom_grades`, the subject scope (`rostered − homeroom_grades`) is always empty** — which means the feature is *not* a grade filter at all. It is: **produce no subject classes and no blended classes; homeroom classes and `Students.csv` are untouched.**

That collapse is the whole design, and it is what makes the change safe. Three guards, no grade comparison anywhere:

1. `classes._run_blended_detection` — return `(class_info_df, BlendedDetection.empty())`.
2. `classes._create_subject_classes` — early return.
3. `enrollments._subject_enrollments` — early return `None`.

**Guard placement is load-bearing** (1): it must sit **after** `normalize_columns` + `filter_excluded_course_codes` (`classes.py:87-94`) and still return the normalized `class_info_df`, because `EnrollmentTransformer._classinfo_coteacher_enrollments` **path 1** (section-letter → homeroom, `enrollments.py:296-321`) consumes that frame and must keep emitting co-teacher rows for K-8 homerooms.

Everything else falls out for free, which is why no other edit is needed:
- `_emit_missing_blended_classes` (`classes.py:249`) iterates empty metadata → no-op.
- `_blended_teacher_enrollments` + co-teacher **path 2** (`enrollments.py:229`, `:324`) read empty maps → inert.
- `assign_class_ids` (`base.py:473`) finds an empty `blended_class_map` → no relabelling.
- Referential integrity is **automatic**: no class and no enrollment is ever created, rather than created-then-suppressed.

**Why this shape rather than the drafted one.** The original draft proposed a `rostered`-grade filter threaded through five call sites plus a schedule pre-filter in `blended.py`. The advisory panel independently found four defects in it, all of which this shape removes *by construction* rather than by careful implementation:

| Drafted-design defect | Why it disappears here |
|---|---|
| Suppression predicate keyed to `homeroom_grades` left straddling blends alive as studentless teacher-only classes — `e187ac8`'s failure class | No predicate; no blend is detected at all |
| `_register_blends` writes `class_map`/`teacher_map` (`blended.py:209,211`) **before** the grade range is known (`:213`) — a natural `continue` yields orphan Class IDs | `_register_blends` is never entered |
| `grade_to_ceds` is **not idempotent** (`KG`/`PK`/`IT`/`PR`/`PS` → `"UG"`); a filter on the wrong side of `split_by_homeroom_grades`'s in-place rewrite (`grades.py:90`) silently deletes Kindergarten | No grade comparison anywhere |
| Filtering the schedule in `_load_reference_frames` changes the per-MT-ID **mode** grade, hence blend identity and naming, and shrinks the fallback section universe | `blended.py` is untouched |

**Alternatives rejected:** a plain boolean `homeroom_classes_only` (equivalent behaviour, but abandons the reserved key name, so per-grade support later means a second key + deprecation); dropping `student_schedule` from SD83's `source_files` (fails — `enrollments.py:33-35` returns empty when the schedule is empty, deleting K-8 homeroom enrollments too); suppressing classes with no student enrollments (silently changes six live districts, and can't distinguish "legitimately empty" from "wrongly rostered").

## Architecture & holistic fit

- **Codebase fit.** Validation at the Pydantic boundary (`models.py`), three guards at the transformer leaves, no district names in code, no new module. `grades.py` and `blended.py` are untouched — which is deliberate: plan 0043's general fix then lands on unmodified blended code with the SD74 golden as its only baseline. The key joins the existing `global_config` opt-in family (`excluded_course_codes`, `cross_enrollment.collapse`, `course_start_grade`) and follows their local-read pattern.
- **Product fit.** SD83's job-to-be-done — SpacesEDU rosters for K-8, myBlueprint+ transcripts for 9-12, no hand-edited CSVs — delivered in config, per CLAUDE.md's configurable-over-hardcoded mandate.
- **Quality dimensions to uphold:**
  - `data-and-persistence` — the config→pipeline key contract (`to_raw_dict`), the versioned-schema gate, referential integrity.
  - `reliability-resilience` — fail-loud validation + a floor against silent filter-to-empty delivery.
  - `maintainability-structure` — three guards, one concept, no new abstraction.
  - `testing` — the default-off proof needs a real positive twin (CLAUDE.md's no-vacuous-greens rule).
  - *Not in scope:* `security` (no new boundary/input), `performance-efficiency` (three early returns), privacy (no new PII surface).
- **Future-proofing.** The key name is reserved for the list form; adding it later is additive (`Literal["homeroom"] | list[str] | None`) with no rename and no deprecation. The first district to need it decides the straddling-blend semantics.

## Affected files

- `src/config/models.py` — `GlobalConfig.class_rostering_grades: Literal["homeroom"] | None = None`; **add the key to `to_raw_dict`'s `global_raw` dict (`:750-767`)**; `model_validator(mode="after")` rejecting the sentinel when `homeroom_grades` is empty.
- `src/config/loader.py:63` — `SUPPORTED_CONFIG_MINOR` 9 → 10 (ETL-affecting key).
- `src/etl/transformers/classes.py` — guards in `_run_blended_detection` + `_create_subject_classes`.
- `src/etl/transformers/enrollments.py` — guard in `_subject_enrollments`.
- `src/etl/pipeline.py` — fail-loud floor: a run that produces neither `Classes` nor `Enrollments` while `Students` is non-empty must not report success (see Risks).
- `config/mappings/sd83myedbc_mapping.yaml` — `class_rostering_grades: "homeroom"`; `version: '1.9'` → **`'1.10'` (quoted)**.
- `tests/test_config.py` · `tests/test_config_version_gate.py` · `tests/test_contract.py` (`TestDistrictQuirks`) · `tests/test_transform_classes.py` · `tests/test_transform_enrollments.py` · `tests/test_blended_classes.py` · `tests/test_zero_orphan_enrollments.py` · a differential test over the SD74 corpus.
- `docs/claugentic-ARCHITECTURE_TREE.md` · `docs/developer/adding-district.md` · `docs/developer/output-contract.md:483` (the SD83 row) · `CLAUDE.md` — one dense clause each.

## Research / grounding

- **Files reviewed:** `classes.py:30-67,87-97,102-185,202-244,249-291` · `enrollments.py:27-73,93-159,164-223,229-246,251-349` · `blended.py:57-93,98-147,184-225,259-282,320-332` · `grades.py:17-93` · `models.py:466-519,750-767` · `loader.py:43-63,98-113` · `pipeline.py:211-220,249,296-315,373-388` · `loader.py:362-399` · `quality/report.py:94-156` · `contract_schema.py:132-145` · `test_contract.py:122-140,183-216,653,736,803-819`.
- **Harness docs consulted:** `docs/claugentic-WORKFLOW.md` · `CLAUDE.md` · `docs/developer/output-contract.md` · `docs/claugentic-DECISIONS.md` · the four Stage-2b lens modules.
- **Findings (verified, not assumed):**
  - `to_raw_dict` hand-enumerates 14 `global_config` keys — a new field is **invisible to the ETL** unless added there. Confirmed by reading `models.py:750-767`.
  - `grade_to_ceds("KG") == "UG"` — confirmed by execution.
  - `_register_blends` map-write ordering — confirmed by reading `blended.py:205-220`.
  - `SUPPORTED_CONFIG_MINOR = 9`; SD83 at `version: '1.9'` — confirmed.
  - `check_delivery_integrity` guards only the `Students` anchor and "no output at all" — empty Classes+Enrollments passes.
  - **Gotcha (out of scope, roadmapped):** `blended.create_name` raises `KeyError: 'course code'` when ClassInformation lacks that column (`blended.py:303`).
  - **Gotcha (pre-existing):** `hc["Grade"]` on a split homeroom takes whichever row survived dedup (`classes.py:169`).

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| **Key ships inert** — added to `GlobalConfig` but not `to_raw_dict`; validates, loads, does nothing | Explicit affected-file entry + a round-trip pin (`load_config("sd83myedbc").to_raw_dict()["global_config"][...] == "homeroom"`), precedent `test_config.py:515`. Plus a completeness test: every non-presentation `GlobalConfig` field appears in `to_raw_dict`. |
| **Older exe silently rosters 9-12** — `MappingConfig` is `extra="ignore"`, so an un-bumped config is read and the key dropped with no warning | Bump `SUPPORTED_CONFIG_MINOR` **and** the config's `version` (quoted `'1.10'`). Pin SD83's declared version ≥ the introducing minor. |
| **Typo'd key silently rosters everyone** — `GlobalConfig` does not forbid extras | Positive pin asserting the key's presence in the shipped SD83 config (doubles as the `to_raw_dict` guard). |
| **Sentinel + empty `homeroom_grades`** ⇒ no homerooms and no subject classes ⇒ empty rostering | Cross-field `model_validator(mode="after")` rejecting the combination at load, with the reason in the message. |
| **Silent non-delivery** — empty Classes+Enrollments passes `check_delivery_integrity` (Students anchor only), previous good files are archived out of the SFTP glob, run reports success/exit 0 | Fail-loud floor in `pipeline.py`: Students non-empty while BOTH rostering entities are empty is a delivery-integrity fault, not a warning. |
| **Orphan regression (`e187ac8`)** | No blend is ever detected, so no map is ever populated — integrity is structural, not enforced. Pinned by a two-sided pairing assertion (below). |
| **`UG` cohort de-rostered** — a student with a blank/unrecognised grade gets a subject class today and nothing under the flag | **Accepted and documented**, not silent: it is inherent to "no subject classes". Log the count; name it in the DECISIONS line and the SD83 rollout note. |
| **First run trips the >20% anomaly** on Classes + Enrollments; Home paints it as a warning | Expected degradation — call it out in the rollout note so nobody "fixes" it. |
| **Partner acceptance unknown** — `Students.csv` will carry a whole 9-12 cohort with **zero** enrollment rows | **Owner/partner confirmation item.** Internal code paths are clean (verified: `report.py:94-156` has no students→enrollments direction; the anchor rule holds), but SpacesEDU has rejected this repo's output before. Raise before SD83's first live delivery. |
| SD83 config unverified against real GDE extracts | Pre-existing for the whole SD83 config; flagged in DECISIONS. |

## Test strategy

**The negative, stated correctly:** the **11** configs that do not set the key are byte-identical — SD83 *is* one of the 12 and is *supposed* to change. The contract sweep asserts **shape only** (entity set, column order, BOM), so the single real content oracle is `tests/test_regression_sd74.py` (156 Classes rows, grades KG-12 + 5 blends). A red `EXPECTED_ENTITIES` row for sd83 is a **design error, never an edit**.

**The positive twin — pinned by value, on the corpus that matters:**
- **Differential over the SD74 corpus** (harness precedent `tests/test_pipeline_parity.py:267-277`): run `tests/snapshots/input/` twice, once with the sentinel injected. Assert exact set deltas — removed Class IDs are exactly the non-homeroom-grade + `BLENDED_` ids; `classes_on ⊂ classes_off`; every removed Class ID also removed from Enrollments; **`Students.csv` `assert_frame_equal`-identical** across both runs. Survives 0043's golden regeneration because it is differential, not absolute.
- **SD83's contract output pinned by value** in `TestDistrictQuirks` (`tests/test_contract.py`), reusing the module-scoped run at **zero** extra pipeline cost. Its fixture has grades 3/10/12: `Classes.csv` Class IDs == exactly the grade-3 homeroom (set equality, not a count); no `MT002_`/`MT003_`; no `BLENDED_`; `Students.csv` User IDs still `{S001,S002,S003}`; `StudentCourses.csv` non-empty and containing S002/S003 (the non-goal, proven).
- **NOT** a new SD83 case in `tests/test_pipeline_e2e_districts.py` — the 12-config sweep already runs sd83 end-to-end (`_create_mbp_all_inputs`), and that module writes a 5-entity input set, so a 7-entity test there means a third copy of the myBlueprint+ fixtures.

**Boundary + wiring:**
- `to_raw_dict` round-trip for the key; completeness test over `GlobalConfig.model_fields`.
- Pydantic: `"none"`/unknown sentinel raises · `""` raises · wrong type (`true`/`123`/`{}`/`["09"]`) raises · `"HOMEROOM"`/`"Homeroom"` — decide and pin · sentinel + empty `homeroom_grades` raises.
- Version gate: SD83's declared version ≥ the introducing minor; `SUPPORTED_CONFIG_MINOR` bumped (note `test_config_version_gate.py` catches a half-bump but is blind to no bump).
- `_base` inheritance: a child config inheriting the key; a child overriding it to `null` — pin whichever semantics deep-merge actually gives.

**Behaviour + integrity:**
- Flag ON: no `BLENDED_` id in Classes **or** Enrollments, asserted **together** (two-sided pairing — `e187ac8` was a pairing failure; `enrolled ⊆ classes` alone is satisfiable by emptiness).
- Flag ON with a **populated** ClassInformation (`Primary Teacher = Y`): co-teacher **path 1** still emits K-8 homeroom teacher rows; **path 2** emits nothing. (The sweep is structurally blind here — SD83's fixture writes an empty ClassInformation, so `detect` early-exits.)
- Flag ON: homeroom classes for grades `["K","1","3"]` are all created (guards against any accidental grade handling).
- **Flag OFF, gate pinned:** an all-homeroom blend is still present in `class_map`/`metadata`/`teacher_map`. This is the only thing proving suppression doesn't fire on the six live districts, and **plan 0043 deletes the golden that currently implies it** — so it must exist as a unit test now.
- Delivery floor: sentinel + empty homeroom set ⇒ loud refusal, not a green run (validator makes it unreachable from YAML; pin the floor at the pipeline level too).

## Decomposition (slices)

- [ ] **Slice 1** — the whole feature: config key + `to_raw_dict` wiring + cross-field validator, version-gate bump, three transformer guards, pipeline delivery floor, SD83 config, tests, docs.

**Why one slice:** splitting config from wiring lands a validated-but-inert key — the exact half-done state the workflow forbids, and the panel's highest-severity finding. ~12 files, three guards, one concept.

---

## Review  _(Stage 3 — plan-gate)_
- **Verdict:** _pending_

### Stage 2b advisory panel (complete — advisory, contributed not gated)
| Lens | Verdict | Disposition |
|---|---|---|
| `yagni-sentinel` | OVER-BUILT (~3×) | **Accepted** — list form cut (owner), dead call sites gone, duplicate e2e cut, minimal-guard design adopted. |
| `maintainability-structure` | GAPS | **Accepted** — `to_raw_dict` (F1), empty-homeroom validator (F2b). F5's `GradeScope` object moot under the simplified design. F11's CLAUDE.md-staleness claim **refuted** (already 12). |
| `data-and-persistence` + `reliability-resilience` | GAPS | **Accepted** — F2 map-ordering, F3 `to_raw_dict`, F4 version gate, F5 idempotency, F6 delivery floor, F7 UG cohort, F9 partner question. F1/F8 moot under the simplified design. |
| `testing` | GAPS | **Accepted** — T1/T15 negative restated, T13 differential twin, T14 placement, T16 flag-OFF gate pin, T2 round-trip, two-sided pairing. |

---

## Spec  _(per slice, after Review passes — Stage 4)_
_Pending Stage-3 gate._
