# 0042 — `class_rostering_grades`: opt-in grade scoping for class rostering

- **Status:** Draft
- **Resumable from:** Slice 1 (not started)
- **Blockers:** none
- **Flags:** none
- **Disposition at close:** single slice — done or deferred per the workflow's lifecycle rule.
- **Roadmap item:** `docs/claugentic-ROADMAP.md` → Remaining backlog (this plan) + a separate Bugs line for the general studentless-blend fix (plan 0043).
- **References:** `docs/claugentic-ARCHITECTURE_TREE.md` · `docs/claugentic-DECISIONS.md` (2026-08-13 SD83 entry) · `docs/developer/output-contract.md` · commit `e187ac8` (the blended-orphan fix this plan must not regress)

## Problem

SD83 (North Okanagan-Shuswap) runs SpacesEDU for K-8 only, but feeds myBlueprint+ course data for grades 9-12. Today there is **no way to express that**: `global_config.homeroom_grades` decides which grades get *homeroom* classes, but every remaining grade automatically gets *subject* classes and enrollments. `src/etl/transformers/classes.py:226` splits the schedule with `keep="subject"` — i.e. everything NOT a homeroom grade — so SD83's grade 9-12 timetable lands in `Classes.csv` and `Enrollments.csv` whether they want it or not.

Three separate paths read grade and must all agree, which is why config alone cannot solve it:

1. `classes.py:226` / `enrollments.py:186` — the subject-class + subject-enrollment split.
2. `classes.py:136` / `enrollments.py:115` — the homeroom split (already grade-scoped).
3. `blended.py:98-124` — blended detection, which reads `student_schedule` + `ClassInformation` and **never applies any homeroom-grade filter**.

Path 3 is the subtle one. Because blended detection ignores `homeroom_grades`, a blend whose grades are *all* homeroom grades produces a class with a teacher and **zero students** — K-8 students only ever receive homeroom enrollments (`enrollments.py:198-204` filters student subject rows to non-homeroom grades), while `_blended_teacher_enrollments` (`enrollments.py:229`) filters nothing. Verified against the SD74 golden:

| Blended class | Grades | Students | Teachers |
|---|---|---|---|
| Business 11 / PHE 9 | 08/11 | 2 | 1 |
| Homeroom 1 / Homeroom 7 / Math 8 / Science 8 | 01/07/08 | 2 | 1 |
| Homeroom 1 / Homeroom 3 / Science 9 | 01/03/09 | 1 | 1 |
| Homeroom 1 / Music 10 | 01/09 | 1 | 1 |
| **Homeroom 2 / Homeroom 4** | **02/04** | **0** | 1 |

The precise rule: **a blended class is studentless iff every one of its grades is a homeroom grade.** Straddling blends (01/09, 08/11) legitimately carry their non-homeroom students and must survive.

Also confirmed empirically (scratchpad probe, sd83myedbc + a 3/4 split sharing one Homeroom value): the homeroom path already merges split grades correctly into one class with both students — so the blended duplicate adds nothing.

## Goals / Non-goals

- **Goal:** an opt-in `global_config.class_rostering_grades` that scopes which grades receive class rostering (Classes + Enrollments), so SD83 emits K-8 homerooms only.
- **Goal:** absent key ⇒ **byte-identical output** for all 12 existing configs. The SD74 golden must not move.
- **Goal:** when the key is set, suppress blended classes whose grades are entirely homeroom grades (owner decision, 2026-08-13) — dropping the class **and** its teacher enrollment together so referential integrity holds.
- **Non-goal:** changing `Students.csv`. Grade 9-12 students MUST stay rostered — `StudentCourses` applies the zero-orphan filter against the active roster, so dropping them would empty the myBlueprint+ transcripts SD83 asked for.
- **Non-goal:** filtering `Staff.csv` (owner decision — staff stay unfiltered, as in every other config).
- **Non-goal:** the **general** studentless-blend fix for the other six districts. That is partner-visible (rewrites the SD74 golden) and is **plan 0043**, deliberately sequenced after this one.
- **Non-goal:** validating `homeroom_grades` itself against the CEDS vocabulary (pre-existing gap → roadmap line).

## Approach

One new `global_config` key with three states, resolved once into a grade allowlist that every rostering path consumes.

```yaml
global_config:
  class_rostering_grades: "homeroom"     # only the grades in homeroom_grades
  # class_rostering_grades: ["09","10","11","12"]   # only these CEDS codes
  # (absent)                              # every grade — today's behaviour
```

**Why the `"homeroom"` sentinel rather than restating the list:** it keeps `homeroom_grades` the single source of grade truth (CLAUDE.md: single source of truth). Restating K-08 in two keys invites silent drift the moment one is edited. The explicit-list form covers the owner's stated future case — a district scoping rosters by grade *without* using homerooms the same way — at the cost of ~15 lines, and avoids a breaking migration from a boolean later.

*(The owner proposed `"none"` for the homeroom-only state; changed to `"homeroom"` because `"none"` reads as "no grades rostered", the opposite of what it means. Owner may override.)*

**Alternatives rejected:**
- A boolean `homeroom_classes_only: true` — simplest, but forecloses the per-grade case and would need deprecating later.
- Drop `student_schedule` from SD83's `source_files` — fails: `enrollments.py:33-35` returns empty when the schedule is empty, deleting the K-8 homeroom enrollments too.
- Suppress classes with no student enrollments — cleaner-sounding, but silently changes six live districts and can't distinguish "legitimately empty" from "wrongly rostered".

## Architecture & holistic fit

- **Codebase fit.** The filter is grade vocabulary, so the resolver + predicate live in `src/etl/transformers/grades.py` beside `split_by_homeroom_grades` and the CEDS table — the module that already owns exactly this concern. Validation lives at the Pydantic boundary (`src/config/models.py`), matching "validate at boundaries". Transformers stay thin: each of the five read sites gains one call, mirroring how `excluded_course_codes` is already threaded through the same three files. **The blended suppression happens at the single detection point** (`_register_blends`), not at each consumer — because `blended_class_map` / `_metadata` / `_teacher_map` all flow from there into `_emit_missing_blended_classes`, `_blended_teacher_enrollments` and `_classinfo_coteacher_enrollments`, so suppressing once keeps every consumer consistent and referential integrity automatic. That is the SOLID answer and the reason this isn't four coordinated edits.
- **Product fit.** SD83's job-to-be-done: SpacesEDU class rosters for their K-8 population, myBlueprint+ transcripts for 9-12, without hand-editing CSVs. This delivers it in config, which is the CLAUDE.md mandate (configurable over hardcoded) and keeps the ETL free of district names.
- **Quality dimensions to uphold:**
  - `maintainability-structure` — one resolver, one predicate, five call sites; no district logic in code.
  - `data-and-persistence` — output correctness + the zero-orphan invariant (no enrollment may reference a dropped class; no class may reference a dropped student).
  - `reliability-resilience` — fail-loud validation (a typo'd grade code must raise at config load, never silently roster nobody).
  - `testing` — the default-off proof is the load-bearing test; it needs a positive twin proving the mechanism works at all (CLAUDE.md's no-vacuous-greens rule).
  - *Not in scope:* `security` (no new boundary/input), `performance-efficiency` (one boolean mask over frames already scanned), privacy (no new PII surface).
- **Future-proofing.** The list form is the open door for per-grade rostering without a schema break. Plan 0043 ungates the blend suppression by deleting a condition, not by rewriting the rule — deliberate, so the general fix is small and reviewable on its own.

## Affected files

- `src/config/models.py` — new `GlobalConfig.class_rostering_grades: str | list[str] | None` + fail-loud validator (sentinel must be `"homeroom"`; list entries must be CEDS codes; empty list rejected).
- `src/etl/transformers/grades.py` — `resolve_rostered_grades(global_config) -> list[str] | None` + `filter_to_rostered_grades(df, grade_col, rostered, *, entity)` (identity when `None`).
- `src/etl/transformers/classes.py` — filter in `_create_homeroom_classes` + `_create_subject_classes`.
- `src/etl/transformers/enrollments.py` — filter in `_homeroom_enrollments` + `_subject_enrollments`.
- `src/etl/transformers/blended.py` — filter the schedule in `_load_reference_frames`; suppress all-homeroom blends in `_register_blends`.
- `config/mappings/sd83myedbc_mapping.yaml` — `class_rostering_grades: "homeroom"`.
- `tests/test_transform_classes.py`, `tests/test_transform_enrollments.py`, `tests/test_blended_classes.py`, `tests/test_config.py`, `tests/test_zero_orphan_enrollments.py` — new coverage.
- `tests/test_pipeline_e2e_districts.py` — the SD83 end-to-end (formalizing the scratchpad probe).
- `docs/claugentic-ARCHITECTURE_TREE.md`, `docs/developer/adding-district.md`, `CLAUDE.md` — document the key.

## Research / grounding

- **Files reviewed:** `classes.py:30-67,102-185,202-244,249-291` · `enrollments.py:27-73,93-159,164-223,229-246,251-349` · `blended.py:57-93,98-124,184-225,259-282` · `grades.py:58-93` · `models.py:395-400` · `contract_schema.py:132-145`.
- **Harness docs consulted:** `docs/claugentic-WORKFLOW.md` (staging, DoD, in-flight split) · `CLAUDE.md` (configurable columns, fail-loud, zero-orphan, no-vacuous-greens) · `docs/developer/output-contract.md` (partner-visible change rule) · `docs/claugentic-DECISIONS.md`.
- **Findings:**
  - `split_by_homeroom_grades` already gives both halves of the split — reuse it, don't reimplement.
  - `excluded_course_codes` is the exact precedent for threading one `global_config` list through `classes`/`enrollments`/`blended` — follow its shape.
  - **Gotcha:** `blended.create_name` raises `KeyError: 'course code'` when `ClassInformation` lacks a Course Code column (`blended.py:303`). Hit while building the probe. Pre-existing, unrelated → roadmap Bugs line.
  - **Gotcha:** `hc["Grade"]` on a split homeroom takes whichever row survived dedup (`classes.py:169`), so a 3/4 split reports one grade. Pre-existing, out of scope.
  - Commit `e187ac8` fixed 195 orphan BLENDED IDs that made **partner ingest reject** SD40's output. Any change here must keep class/enrollment emission in lockstep — this plan does so by suppressing at the single detection point.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Existing districts' output changes | Absent key = identity in the resolver; SD74 golden left untouched and must stay green; the 12-config contract sweep asserts the emitted set + column order. |
| Blend suppression too aggressive — kills a blend carrying real students | Rule is `grades ⊆ homeroom_grades`, verified against the SD74 golden's 4 straddling blends; explicit test that a mixed blend survives. |
| Orphan enrollments reintroduced (regressing `e187ac8`) | Suppress once at detection so every downstream map is consistent; extend `test_zero_orphan_enrollments.py`; the contract sweep's every-Enrollments-ID-exists-in-Classes invariant already guards it. |
| Typo'd grade code silently rosters nobody | Pydantic validator rejects non-CEDS codes at load with the valid set in the message. |
| SD83 config unverified against real GDE extracts | Already true of the whole SD83 config; flagged in DECISIONS. Revisit when real exports land. |

## Test strategy

**The load-bearing test is the default-off proof**, and per CLAUDE.md's no-vacuous-greens rule it needs a positive twin:

- **Negative:** all 12 configs unchanged — SD74 golden byte-identical, contract sweep green.
- **Positive twin:** the same fixture *with* the key set produces a demonstrably different, smaller `Classes.csv` — proving the mechanism is wired, not inert.

Plus:
- `resolve_rostered_grades` unit table: absent → `None` · `"homeroom"` → the homeroom list · explicit list → itself.
- Pydantic: `"none"`/unknown sentinel raises · non-CEDS code raises · empty list raises · valid forms load.
- Classes: SD83 shape → homeroom classes only; no `BLENDED_` row; grade-10 subject class absent.
- Enrollments: homeroom student + teacher rows present; no subject/blended rows; **grade-10 student still in `Students.csv`**.
- Blended: all-homeroom blend suppressed; straddling blend (01/09) survives with its student.
- Zero-orphan: every Enrollments Class ID exists in Classes under the new flag.
- E2E: `sd83myedbc` still emits all 7 entity CSVs.

## Decomposition (slices)

- [ ] **Slice 1** — the whole feature: config key + validator, resolver + filter helper, five call sites, blended suppression, SD83 config, tests, docs.

**Why one slice:** splitting config-model from transformer-wiring would land a validated-but-inert key — a half-done state the workflow forbids. ~11 files, one cohesive mechanism, well-understood after the probe; comfortably one session.

---

## Review  _(filled by synthesizer-gate in its plan-gate altitude, Stage 3)_
- **Verdict:** _pending_

---

## Spec  _(per slice, after Review passes — Stage 4)_
_Pending review._
