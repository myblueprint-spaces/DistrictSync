# 0028 — SD60 (Peace River North) district config + config-driven Family filter

- **Status:** Draft (awaiting user approval)
- **Resumable from:** §Decomposition Slice 1
- **Blockers:** none
- **Flags:** none
- **Roadmap item:** n/a (new district onboarding)
- **References:** `docs/claugentic-ARCHITECTURE_TREE.md` · `docs/claugentic-DECISIONS.md` · sibling configs `config/mappings/sd74myedbc_mapping.yaml`, `sd54myedbc_mapping.yaml`

## Problem
SD60 (Peace River North) supplied 6 MyEd BC GDE files and needs a district config so the district appears in the desktop UI picker and converts to SpacesEDU CSVs. The data profiling surfaced three realities requiring decisions (all now decided by the user):
1. **Cross-enrollment** — 386 students are `Active` at two schools → ~388 duplicate `User ID` rows in `Students.csv`. `students.py` never dedupes. **Decision: ship duplicates as-is** (SpacesEDU merges by User ID).
2. **Family = all emergency contacts** — `EmergencyEnhanced.txt` has 23,758 rows incl. grandmothers/aunts/"Friend of Family"; `family.py:12-16` emits every row with no filter and no config knob. **Decision: guardians only** (`Parent Auth / Guardian = Y`, 11,959 rows) → needs a new config-driven filter.
3. **Blank student emails** — 729 active students (~8%) have no email; 70+ domains so no template is viable. **Decision: keep them, email blank** (base direct-column behavior — no change).

Everything else lines up with base MyEd BC canonical names (verified): `KF/EL/SU`→`KG/KG/UG` (`base.py:77`), DOB `DD-MON-YYYY` parses (`base.py:313` `%d-%b-%Y`), case-insensitive column match (`base.py:802,827`), `map_role` `Y`→teacher (`base.py:94`), section comes from the schedule's `Section` not `Section Letter` (`classes.py:200,312`; `base.py:608`).

## Goals / Non-goals
- **Goal:** `sd60myedbc` config that converts all 6 SD60 files correctly; SD60 appears in the UI district picker (auto-discovered).
- **Goal (feature 1):** Family limited to guardians via a reusable, config-driven `row_filters`.
- **Goal (feature 2):** opt-in, config-driven cross-enrollment collapse — dedupe the ~386 dual-school students to ONE `User ID`, keeping the **home-school** row (`Home school number`), preserving class enrollments at both schools. Data-backed (2026-07-07): both rows share the same admission date, neither has a withdraw date, classes are DISJOINT at each school, and `Home school number` uniquely picks one of the two for all 386 — so it's genuine dual-enrolment and home-school is the principled canonical row. Safe under either SpacesEDU importer behavior (merge OR reject). Off for every other district.
- **Goal:** include `Active No Primary` in Students `active_values` (34 students are exclusively ANP — cross-enrolled, primary elsewhere; base filter would drop them).
- **Goal:** wiring + docs so gates stay green and counts stay accurate.
- **Non-goal:** myBlueprint+ (CourseInfo/StudentCourses) for SD60 — rostering 5 entities only (base default), matching sibling SpacesEDU districts.
- **Non-goal:** email regeneration / format override — keep file emails (blanks kept); pending SD60 clarification on the email source-of-truth.
- **Non-goal:** e2e/contract synthetic fixtures for SD60 (pre-existing 3-of-6 coverage debt per ROADMAP:64 — not this slice).

## Approach
Two pieces:
1. **Config-driven Family row filter (small reusable feature).** Add a typed `RowFilter` to the config model and an `EntityConfig.row_filters` list; apply it in `family.py` via a new reusable `BaseTransformer.apply_row_filters(...)`. Config-driven (not a hardcoded `"Parent Auth / Guardian"=="Y"` in code) because the **Configurable Columns core rule** forbids hardcoded source columns. Fail-loud if the configured column is absent (validate at boundaries). Wired into Family only (YAGNI — helper lives on base, reusable, but not speculatively wired elsewhere).
   - *Rejected:* hardcode the filter in `family.py` (violates Configurable Columns). *Rejected:* generic filter applied to every entity in the pipeline dispatch (broader blast radius; only Family needs it).
2. **`config/mappings/sd60myedbc_mapping.yaml`** modeled on SD74 (same `StudentCourseSelection.txt`-as-schedule shape): `source_files` overrides, `section letter → Section`, `course title → Title`, `primary teacher flag → ""`, Enrollments `student_id_col: Student Number`, `excluded_course_codes: [ATT--AM, ATT--PM]`, `school_year_sources.student_schedule: StudentCourseSelection.txt`, Family `row_filters: [{column: "Parent Auth / Guardian", include: ["Y"]}]`. Students/Staff inherit base field_map unchanged (columns match case-insensitively); Email inherits base `Student email address` column (keep-blank).

## Architecture & holistic fit
- **Codebase fit** — filter is a `BaseTransformer` helper (DRY/reuse), invoked by `FamilyTransformer`; config schema stays in `models.py` (single source), round-tripped through `to_raw_dict()`. No layering violations (config-data ↔ ETL-business boundary preserved).
- **Product fit** — SpacesEDU "Family" = parent/guardian accounts; filtering out non-guardian emergency contacts is the correct product semantics. New district ships end-to-end for a real customer.
- **Quality dimensions to uphold** (→ `docs/claugentic-standards/` modules): `data-and-persistence` (correct filtered roster; deterministic), `reliability-resilience` (fail-loud on missing filter column, not silent all/none), `maintainability-structure` (config-driven, reusable helper, single source), `security` (no PII in logs — filter logs counts only), `testing` (unit coverage for the filter + config round-trip + SD60 load), `product-ux` (correct Family population + UI picker discovery).
- **Future-proofing** — `row_filters` is a list (AND-combined) and lives on `EntityConfig`, so any district/entity can add filters later without a model change; keep `include`-only for now (add `exclude` only when a real case appears).

## Affected files
- `src/config/models.py` — add `RowFilter` model (`extra="forbid"`); `EntityConfig.row_filters: list[RowFilter]`; emit in `to_raw_dict()` entity entry.
- `src/etl/transformers/base.py` — new `apply_row_filters(df, filters, entity_name)` (case-insensitive column + value match; fail-loud on missing column; log dropped count, no PII).
- `src/etl/transformers/family.py` — apply `row_filters` before `apply_field_map`.
- `config/mappings/sd60myedbc_mapping.yaml` — NEW config.
- `Makefile:23` — add `'sd60myedbc'` to `validate-config`.
- `.github/workflows/ci.yml:45` — add `'sd60myedbc'` to CI validate list.
- `docs/claugentic-ARCHITECTURE_TREE.md` — new config line (git-hook enforced) + note `row_filters` on models/family/base entries.
- `docs/claugentic-DECISIONS.md` — dated entry (SD60 config; guardians-only filter; ship-duplicates declined-dedup).
- `CLAUDE.md` — "6→7 SpacesEDU district configs", validate-config count 10→11, add `row_filters` to the Family/Configurable-Columns notes.
- `README.md` district table · `docs/developer/adding-district.md` reference table · `docs/partner/installation.md` district notes — add SD60 row.
- `tests/test_transform_family.py` (+ `tests/test_config.py`, `tests/test_ui_flet_humanize.py`) — filter tests + SD60 parity cases.

## Research / grounding
- **Files reviewed:** `config/mappings/{myedbc,sd74myedbc,sd54myedbc,sd48myedbc}_mapping.yaml`; `src/config/models.py` (EntityConfig extra=ignore → must add field + to_raw_dict); `src/etl/transformers/{students,family}.py`; plus audited `base.py`/`classes.py`/`enrollments.py` behavior (grade map, DOB parse, case-insensitivity, section source, map_role) and full UI/wiring touchpoint sweep.
- **Findings:** base is a clean parent; only Family needs a code feature; UI picker auto-discovers from `config/mappings/` via `available_configs()` + YAML `district_name` (no catalog edit); `--sis` is regex-validated (no allowlist); SD74 snapshot test is frozen (unaffected).

## Risks & mitigations
- **New `row_filters` schema breaks existing config validation** → `extra="forbid"` only on `RowFilter`; `EntityConfig.row_filters` defaults empty, so all existing configs validate unchanged. Add a models round-trip test.
- **Filter silently drops everyone / no one if MyEd renames the column** → fail-loud raise with available-columns hint; unit test for missing column.
- **Duplicate Students rows are intentional but look like a bug** → documented in config comment + DECISIONS declined-dedup line; quality report still flags them (informational).
- **Changing `family.py` could regress other districts** → filter is inert when `row_filters` absent (all siblings unaffected); full test suite + `make validate-config` gate.

## Test strategy
- Unit: `apply_row_filters` keeps only matching rows; case-insensitive column + value; multiple filters AND-combine; missing column raises; empty filters = passthrough. `family.py` end-to-end with a guardian flag. Models: `RowFilter` validation + `to_raw_dict()` round-trip + `_base` merge.
- Config: `load_config("sd60myedbc")` valid; inherits 5 rostering entities; Family carries the filter.
- Gate suite: ruff (check+format), mypy (non-UI), bandit, pytest ≥80%, `make validate-config`, tree-check.
- Runtime verify (the real proof): run the pipeline on `data/input` with `--sis sd60myedbc --dry-run`; assert Family rows ≈ 11,959 (Auth=Y), Students emits duplicates, all 5 CSVs produced, no crash.

## Decomposition (slices)
- [ ] **Slice 1 — Config-driven Family row filter (feature).** `models.py` + `base.py` + `family.py` + unit/model tests. Lands complete: inert by default, fully tested, no config depends on it yet.
- [ ] **Slice 2 — SD60 config + wiring + docs + verify.** `sd60myedbc_mapping.yaml` (uses Slice-1 filter), Makefile/CI, ARCHITECTURE_TREE, DECISIONS, CLAUDE.md/README/docs, parity tests, real-pipeline dry-run. Lands complete: gates green + SD60 converts + appears in UI.

_(Both slices land on branch `feat/sd60-mapping` off `feat/flet-ui-rebuild`. Pre-existing uncommitted harness changes in the working tree are kept OUT of these commits.)_
