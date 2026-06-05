# 0003 — Active-Student Filter: Zero-Orphan Enrollments + Config-Driven Predicate

- **Status:** Implemented + merged onto main on branch `active-student-filter` (3 slices + PreReg correction + DRY cleanup; architect-reviewed; 752 tests green; SD48 verified 7,543 roster / 0 orphans). NOTE: PreReg was **excluded** by default post-spec (per `docs/partner/faq.md`), correcting the plan's earlier "retain PreReg" — see DECISIONS.
- **Roadmap item:** docs/ROADMAP.md → (new) "Orphaned enrollments for inactive students"
- **References:** `docs/ARCHITECTURE_TREE.md` · `docs/DECISIONS.md` · `CLAUDE.md` (Configurable Columns, Engineering Principles) · commit `8e1754b` (homeroom-enrollments fix that exposed this)
- **Verified evidence (SD48, districtsync_sd48_2026-06-01):** 405 orphaned student enrollments (401 homeroom + 4 subject); 50 inactive students wrongly rostered (Withdrawn-without-date 38, Inactive 6, Graduate 6).

## Problem
Two linked defects in how "active student" is determined and applied.

**1. Orphaned enrollments — outputs reference students absent from `Students.csv`.**
`Students` is filtered to active-only, but the enrollment/class paths that read the **demographic** frame filter only by *grade*, not by active status:
- `classes.py:_create_homeroom_classes` ([classes.py:104](src/etl/transformers/classes.py)) builds homeroom classes from `grade ∈ homeroom_grades` demographic rows — active or not.
- `enrollments.py:_homeroom_enrollments` ([enrollments.py:96](src/etl/transformers/enrollments.py)) builds homeroom student rows the same way.
- `enrollments.py:_subject_enrollments` ([enrollments.py:163](src/etl/transformers/enrollments.py)) builds subject student rows from the **schedule**, also unfiltered.

Result: withdrawn/inactive students get enrollment rows whose `User ID` matches no row in `Students.csv` → import noise/errors in SpacesEDU. This was newly *exposed* by `8e1754b` (which correctly restored homeroom enrollments); the homeroom path contributed 401 of the 405 orphans.

**2. Active-detection is wrong and hardcoded (violates Configurable Columns + Fail-loudly).**
`students.py._determine_enrollment_status` ([students.py:32](src/etl/transformers/students.py)):
- Checks the **British** column `"enrolment status"` (one L). Spellings vary in the wild: **SD48's actual MyEd export header is two-L `"Enrollment status"`** (verified byte-for-byte), while the repo's fixtures and SD40's injected headers use one-L `"Enrolment Status"`. SD48's real two-L data therefore never matches the one-L check and falls through to withdraw-date-only — admitting 50 non-active students (`Withdrawn`/`Inactive`/`Graduate`) that lack a withdraw date. The one-L **fixtures match the buggy code, which is why tests never caught it.** The fix resolves an **alias set** covering both spellings, overridable per district (see Spec).
- `_filter_active` keeps only `EnrollStatus == "Active"` → **`PreReg` students are dropped**, contradicting the documented intent (the config comment "auto detected from Enrollment Status or Withdrawal Date" and the status branch's own `["Active","PreReg"]` allow-list).
- The status/withdraw **column names are hardcoded** in transformer code — the project's Configurable Columns rule says source columns must resolve from the district `field_map`.

## Goals / Non-goals
- **Goal (invariant):** No output references a student absent from `Students.csv`. Concretely: **zero** orphaned student enrollments (homeroom + subject), and no homeroom class built solely from inactive students.
- **Goal (single source of truth):** One config-driven "is active" predicate in `BaseTransformer`, reused by Students (roster), Classes (homeroom), Enrollments (homeroom + subject). No duplicated/re-implemented active logic.
- **Goal (correct detection):** Honor the status column via its **corrected, config-resolved** name; combine with withdraw-date as a **hard override** (a past withdraw date wins over an active status); keep `PreReg` active.
- **Goal (config-driven):** `status_column` / `withdraw_date_column` / `active_values` resolve from the Students `EnrollStatus` config, with MyEd defaults so the existing 8 configs need no edits; districts can override.
- **Non-goal:** `Family.csv` orphans. Family references `Student Number` from `EmergencyContactInformation.txt` (not the demographic file), so it's outside "every place we read demographic." Same noise class → **ROADMAP** (note below), unless explicitly pulled in.
- **Non-goal:** Re-deriving active status from the schedule's own `Status` column. Subject enrollments are filtered against the **active roster set**, not re-classified.
- **Non-goal:** Filtering **subject** class creation (schedule-driven; not a demographic read). Only homeroom class creation is filtered.
- **Non-goal:** Per-district `active_values` tuning beyond the default (overridable later).
- **Non-goal:** Changing grade/homeroom/blended logic.

## Approach
**Slice 1 — one correct, config-driven predicate.** Add to `BaseTransformer` a single active-detection unit that (a) resolves `status_column`, `withdraw_date_column`, `active_values` from the Students `EnrollStatus` config or defaults, (b) computes the per-row `EnrollStatus` label, and (c) computes an `is_active` boolean mask:

```
active = (status_col missing/blank  OR  status_value ∈ active_values)
         AND NOT past_withdraw_date(withdraw_value)   # hard override
```

Defaults (bare-null `EnrollStatus` sentinel): `status_column` = first column **present in the frame** from the alias `["enrollment status", "enrolment status"]` (covers real two-L MyEd exports *and* the repo/SD40 one-L spelling; `None` if neither → date-only); `withdraw_date_column="withdraw date"`; `active_values=["Active","PreReg"]`. `past_withdraw_date` keeps the existing 4-format parsing; unparseable non-blank date → inactive + warn. `students.py` refactors `_determine_enrollment_status`/`_filter_active` to call this (no behavior duplicated). A new strict `FieldEnrollStatus` Pydantic model (`extra="forbid"`) validates the dict form and **fails loudly** on an unknown key — today an unknown-shaped dict only warns and passes through (`models.py:124`).

**Slice 2 — publish the roster, filter against it.** `Students` publishes `context.active_student_ids` (normalized `Student Number` strings of active students) built from the **same mask**. Then:
- `classes.py:_create_homeroom_classes` filters the demographic frame to active rows before building homerooms (no empty homeroom classes).
- `enrollments.py` filters homeroom (demographic, by `demo_student_col`) **and** subject (schedule, by `student_id_col`) student rows to the active set. Teacher rows untouched.
- **Guard:** if `active_student_ids` is empty (Students disabled / ran later), skip filtering and log a WARNING — never filter-to-empty.

Why a shared set works across both paths: the schedule's `Student ID` values are the same pupil numbers as the roster's `Student Number` (pre-fix only 4 of ~2,300 subject students were non-matching). Normalize both sides with `astype(str).str.strip()`.

Alternatives rejected: re-implementing an "is active" check at each read site (drifts from the roster — violates DRY); filtering at the extractor/raw-load boundary (extractor is entity-agnostic; status columns are a Students concern — wrong layer); re-classifying the schedule via its own `Status` column (two sources of truth for "active").

## Key decisions to confirm at the spec gate
1. **SD48 roster shrinks 7,922 → 7,872 (−50).** All 50 are verifiably inactive (`Withdrawn` 38 / `Inactive` 6 / `Graduate` 6, currently leaking in via the column-name bug). Correct, but a **visible output change** — needs sign-off. (The frozen **SD74 snapshot stays byte-identical** — its demographic fixture has no status column and all-blank withdraw dates — so it's a green regression guard, *not* a regen target. Movement there would mean the date-only back-compat path is wrong.)
2. **`active_values` default = `["Active","PreReg"]`.** `"Active No Primary"` excluded (moot for SD48 — all 27 already have withdraw dates). Overridable per district.
3. **Withdraw date is a hard override** even when status is active (the 5 SD48 "active status + past withdraw date" cases stay inactive).

## Affected files
- `src/etl/transformers/base.py` — new active-detection predicate (single source of truth: label + mask + column/value resolution).
- `src/etl/transformers/students.py` — refactor to use the predicate; publish `context.active_student_ids`.
- `src/etl/transformers/context.py` — add `active_student_ids: set[str] = field(default_factory=set)`.
- `src/etl/transformers/classes.py` — filter homeroom creation by the active set (resolve demo student col from Students config).
- `src/etl/transformers/enrollments.py` — filter homeroom + subject student rows by the active set; empty-set guard.
- `src/config/models.py` — accept/validate the `EnrollStatus` dict (`status_column`, `withdraw_date_column`, `active_values`); preserve the bare-null sentinel; `to_raw_dict` passthrough.
- `config/mappings/myedbc_mapping.yaml` — keep `EnrollStatus:` null (defaults apply); add a clarifying comment documenting the resolved defaults. (No per-district edits required.)
- `tests/` — predicate units; config-load; orphan-regression; classes/enrollments filter + guard. **Plus inverting existing tests that lock pre-fix behavior:** `tests/test_enrollment_status.py` (`test_prereg_kept` requires PreReg filtered), `tests/test_transform_students.py:13,35` (PreReg-filtered + one-L status assumptions), `tests/conftest.py` (shared fixtures), `tests/test_config.py` (add malformed-`EnrollStatus` negative). Audit `tests/test_pipeline_e2e_districts.py` for count assertions that shift.
- Docs: `docs/DECISIONS.md`, `CLAUDE.md`, `docs/ENGINEERING_STANDARDS.md` (data-integrity dimension), `docs/ROADMAP.md`, `docs/ARCHITECTURE_TREE.md` (refresh descriptions for the 5 touched modules).

## Risks & mitigations
- **Roster change surprises a district.** → Document in DECISIONS + release note; keep/upgrade the existing "Filtered N inactive students" log to name the status breakdown; the removed rows are verifiably inactive. **SD74 snapshot must stay green/byte-identical** (date-only back-compat); investigate any movement as a predicate bug.
- **Roster vs published-set divergence.** → Build the set from the *same* mask function; add a test asserting `active_student_ids == set(Students.User ID)` for a fixture.
- **Ordering dependency (Students must precede Classes/Enrollments).** → Confirmed by entity order (Students→…→Classes→Enrollments). Add the empty-set guard + warning as defense.
- **Schedule `Student ID` vs roster `Student Number` key mismatch.** → Same pupil-number values (verified). Normalize both; test with matching + non-matching fixtures.
- **Config back-compat.** → Bare `EnrollStatus:` null still valid (defaults); dict optional; **all 8 configs must still load** (CI `make validate-config`). Unknown EnrollStatus keys → fail loudly at load.
- **`mbp_core`** (Students + course CSVs, no Classes/Enrollments) → `active_student_ids` simply unused; verify no crash. `StudentCourses` is **not** filtered by the set (out of scope) — note in DECISIONS.
- **PreReg now retained** could *add* students vs today's `== "Active"` filter. On SD48 PreReg=329, none with past withdraw dates → all retained. Confirm intended (it matches documented intent). Flag for sign-off.

## Test strategy
- **Predicate units (base):** status active→active; status inactive→inactive; `PreReg`→active; no status col (date-only) back-compat; **conflict** (active status + past withdraw → inactive); unparseable non-blank date → inactive + warning; neither column present → default active + warning; custom `active_values`; custom column names.
- **Config-load:** `EnrollStatus` null (defaults) and dict (overrides) both validate; malformed dict / **unknown key raises** (new `FieldEnrollStatus`, `extra="forbid"`) — add the negative case to `tests/test_config.py`; all 8 real configs validate.
- **Inverted existing tests:** `test_enrollment_status.py::test_prereg_kept` → PreReg retained; `test_transform_students.py:13,35` PreReg/one-L assumptions; document each inverted expectation in its docstring citing the PreReg-retained DECISIONS line.
- **Invariant / orphan regression:** fixture demographic (active + withdrawn homeroom students) + schedule (a withdrawn secondary student); assert Enrollments has **zero** student rows with `User ID ∉ Students.User ID`; assert homeroom classes built only from active students; assert teacher rows unchanged; assert guard (empty set → no filtering + warning).
- **Gates:** full suite + 80% coverage; ruff check + format; mypy (non-UI); bandit; `make validate-config`; **SD74 snapshot expected-green (byte-identical)** — not regenerated.

## Decomposition (slices)
- [ ] **Slice 1 — Config-driven active predicate** (`base.py`, `students.py`, `models.py`, `myedbc_mapping.yaml` comment, tests, DECISIONS). Lands complete: defines the predicate contract; corrects roster. Self-contained.
- [ ] **Slice 2 — Zero-orphan invariant** (`context.py`, `students.py` publish, `classes.py`, `enrollments.py`, tests). Depends on Slice 1. Lands complete: invariant + guard + regression tests.
- [ ] **Slice 3 — Docs & decisions** (orchestrator): ARCHITECTURE_TREE refresh, CLAUDE.md note, ENGINEERING_STANDARDS data-integrity dimension, ROADMAP (Family orphans; per-district `active_values`), release note for the −50 roster change.

---

## Spec

### Slice 1 — Config-driven active predicate  *(implementer-architect)*
**`src/etl/transformers/base.py`** — add (single source of truth):
- `resolve_active_config(students_field_map, df_columns) -> tuple[str|None, str, list[str]]`: read `students_field_map.get("EnrollStatus")`; if a dict, pull `status_column`/`withdraw_date_column`/`active_values`; else defaults — `status_column` = first of `["enrollment status", "enrolment status"]` **present in `df_columns`** (alias; `None` if neither → date-only branch), `withdraw_date_column="withdraw date"`, `active_values=["Active","PreReg"]`. Column names lower-cased to match normalized frames. A configured `status_column` string is used verbatim (still presence-checked).
- `compute_enroll_status(df, students_field_map) -> pd.Series`: per-row label in {`"Active"`,`"PreReg"`,`"Inactive"`}. Logic: if `status_col` present → label from value (value if ∈ `active_values` else `"Inactive"`); elif `withdraw_col` present → date logic; else `"Active"` + one warning. Then **downgrade to `"Inactive"` if `past_withdraw_date` is true** (hard override) regardless of the status branch. Reuse a shared `past_withdraw_date(value, today)` helper (the existing 4 formats; unparseable non-blank → True + collect for one aggregated warning).
- `is_active_mask(df, students_field_map) -> pd.Series[bool]`: `compute_enroll_status(...) != "Inactive"`. (Label and mask share one function; no `∪ {"Active"}` union — so a district that drops `"Active"` from `active_values` is honored.)

**`src/etl/transformers/students.py`** — replace `_determine_enrollment_status` + `_filter_active` bodies with calls to the base predicate (resolve `field_map` for "Students"). Preserve the `EnrollStatus` output column and the "Filtered N inactive students" log (extend to include a status breakdown when available). PreReg rows are retained.

**`src/config/models.py`** — add a strict `FieldEnrollStatus(BaseModel)` with `model_config = ConfigDict(extra="forbid")` and optional `status_column: str|None`, `withdraw_date_column: str|None`, `active_values: list[str]|None`. In `classify_field` (`:92`) add a branch **before** the warn-passthrough fallback (`:124`), keyed on `"active_values" in raw or "status_column" in raw or "withdraw_date_column" in raw` (collision-free vs `format`/`value`/`transform`/`student_id_col`+`staff_id_col`/`primary teacher flag`/`column`). Add the matching `get_raw_field_map` round-trip branch (`:335`) so the pipeline receives a raw dict. `EnrollStatus: null` stays valid; a malformed/unknown-key dict now **raises** at load (current raw-passthrough does NOT raise — that's the bug being closed).

**`config/mappings/myedbc_mapping.yaml`** — keep `EnrollStatus:` null; replace the comment with the resolved defaults (status-column alias `["Enrollment status","Enrolment status"]`, `withdraw_date_column="Withdraw date"`, `active_values=["Active","PreReg"]`, withdraw-date hard override).

**Tests:** predicate units + config-load (above) **and invert the existing pre-fix tests** — `test_enrollment_status.py::test_prereg_kept` (PreReg retained), `test_transform_students.py:13,35`, conftest fixtures, malformed-`EnrollStatus` negative in `test_config.py`; audit `test_pipeline_e2e_districts.py`. **Acceptance:** SD48-shaped fixture yields the corrected active set (`Withdrawn`/`Inactive`/`Graduate`-without-date removed; `PreReg` kept; active-status+past-withdraw removed); the one-L fixture column is now honored; all 8 configs load; SD74 snapshot byte-identical; full suite green.

### Slice 2 — Zero-orphan invariant  *(implementer-architect, after Slice 1)*
**`src/etl/transformers/context.py`** — add `active_student_ids: set[str]` (default empty set).

**`src/etl/transformers/students.py`** — after filtering, set `context.active_student_ids = set(working[<student-number col>].astype(str).str.strip())`, where the student-number col resolves from `Students.field_map["User ID"]` (normalized). (Same value space as `Students.User ID`.)

**`src/etl/transformers/base.py`** — add `filter_to_active(df, student_col, context) -> df`: if `context.active_student_ids` is non-empty, return rows where `df[student_col].astype(str).str.strip()` ∈ the set; else log a WARNING (`"active_student_ids empty — skipping active filter"`) and return `df` unchanged.

**`src/etl/transformers/classes.py`** — in `_create_homeroom_classes`, after loading/normalizing the demographic frame and before grade selection, `filter_to_active(...)` on the demo student-number col (resolve from Students config, mirroring enrollments). Prevents empty homeroom classes.

**`src/etl/transformers/enrollments.py`** — apply `filter_to_active(...)`:
- homeroom: filter `student_demo_df` (or the post-merge `valid`) by `demo_student_col` before building `student_enroll`.
- subject: filter `non_homeroom` by `student_id_col` before building subject `student_enroll`.
- Leave teacher/co-teacher rows untouched.

**Tests:** orphan-regression + guard (above), plus `active_student_ids == set(result["User ID"])` for a fixture, plus a one-liner asserting the `_classinfo_coteacher_enrollments` (teacher-only) path is unaffected. **Acceptance:** for an SD48-shaped fixture, **zero** student enrollments reference a non-rostered student; homeroom + subject both clean; teacher/co-teacher rows unchanged; empty-set guard leaves rows intact and warns.

### Slice 3 — Docs & decisions  *(orchestrator)*
- `docs/DECISIONS.md`: config-driven active predicate (corrected `Enrollment status` column, withdraw-date hard override, `PreReg` retained, `active_values` default); zero-orphan invariant via published active roster; SD48 −50 roster correction; `StudentCourses`/`Family` explicitly out of scope.
- `CLAUDE.md`: note the active predicate is centralized in `base.py` and config-driven via `EnrollStatus`; enrollments/classes filter against `context.active_student_ids`.
- `docs/ENGINEERING_STANDARDS.md`: add/extend a **data-integrity** dimension — "referential integrity: emitted rows must not reference entities filtered from their parent roster."
- `docs/ROADMAP.md`: LATER — `Family.csv` active filtering; per-district `active_values` overrides; optional StudentCourses active filtering.
- `docs/ARCHITECTURE_TREE.md`: refresh one-line descriptions for `base.py`, `students.py`, `context.py`, `classes.py`, `enrollments.py`.
- Release note: SD48 (and possibly other districts) will show fewer students/enrollments — inactive records that were previously included in error.

---

## Review

**Verdict: REQUEST-CHANGES.** The approach is sound, well-sliced, and the orphan/predicate decomposition is correct. But two blocking issues will produce a red suite as written, and two factual claims used to justify the plan don't survive a read of the code/fixtures. All are cheap to fix in the spec; none change the architecture.

### Blocking

**B1 — The new default `status_column="Enrollment status"` (two L) silently disables status detection for every fixture and most real configs, because the codebase standard is the one-L `"enrolment status"`.**
The Problem section frames "two L vs one L" as the bug. That is backwards for *where the column name is consumed*. After `normalize_columns`, the current code matches `"enrolment status"` (one L) — and that one-L spelling is what every test and the only real status-bearing config use:
- conftest `student_demographic_df` ([conftest.py:185](tests/conftest.py)) and `student_demographic_with_withdraw_df` use `"enrolment status"`.
- `test_pipeline_e2e_districts.py:32` injects `"Enrolment Status"` (one L).
- SD40's headerless schedule injects `"Enrolment Status"` (one L) at [sd40myedbc_mapping.yaml:62](config/mappings/sd40myedbc_mapping.yaml) and :93.
The claim that "MyEd BC's real header is `Enrollment status` (two L)" is asserted but **not verifiable anywhere in this repo** — every committed artifact uses one L. The SD48 evidence (50 inactive leaking in) is consistent with *either* spelling, so it does not establish the two-L claim. If you ship `active_values`-detection keyed on two-L while fixtures/configs carry one-L, the predicate falls through to the withdraw-date branch for all of them and the "honor the status column" goal is silently NOT met in tests — you'd get a green suite that proves nothing, or (B2) a red one. **Required:** before finalizing, confirm the real SD48 MyEd BC demographic header byte-for-byte. If it is genuinely two-L, the default must match **both** spellings (e.g. resolve against a small alias set `{"enrollment status", "enrolment status"}`, or default the column to the one-L spelling already proven in-repo and let two-L districts override). Pick one and write it into the Slice-1 spec; do not leave the implementer to guess. This is the single highest-impact correctness item.

**B2 — Existing tests lock the *old* behavior the plan reverses; they are not in the Affected-files/Test-strategy lists, so the suite goes red.**
- `test_enrollment_status.py::test_prereg_kept` ([test_enrollment_status.py:37](tests/test_enrollment_status.py)) asserts `len(result) == 0` — i.e. it *requires* PreReg to be filtered out. The plan retains PreReg, so this test must be inverted.
- `test_transform_students.py::test_full_student_transform` ([test_transform_students.py:13](tests/test_transform_students.py)) asserts the kept count equals `== "Active"` rows, explicitly noting "PreReg Grace is filtered" — also breaks once PreReg is retained.
- `test_transform_students.py::test_inactive_students_excluded` ([test_transform_students.py:35](tests/test_transform_students.py)) feeds one-L `"enrolment status"` with no withdraw date; under a two-L default (B1) the Inactive row would no longer be detected and the assert `len==2` breaks.
**Required:** add `tests/test_enrollment_status.py`, `tests/test_transform_students.py`, and `tests/conftest.py` to Slice 1's Affected files, and state in the Test strategy that the PreReg-filtered assertions are intentionally inverted (cite the DECISIONS line for PreReg-retained). A "Definition of Done = no new tech debt + all gates green" plan cannot omit the tests it invalidates.

### Should-fix (not blocking, but will mislead the implementer)

**S1 — The SD74 snapshot claim is almost certainly false and will waste a regeneration cycle.** The plan states (lines 56, 72, 84) "SD74 snapshot must be regenerated … confirm diff is inactive-only." But the frozen SD74 demographic input [tests/snapshots/input/StudentDemographicInformation.txt] has **no status column** (neither spelling) and **zero non-blank withdraw dates** across all 100 rows (verified). Under the new predicate every snapshot student stays Active, so `Students.csv` and all downstream enrollments should be **byte-identical** — the snapshot should NOT move. Reframe: the SD74 gate is a *regression guard you expect to stay green*, not a snapshot you regenerate. If it *does* move, that's a real bug in the predicate's date-only back-compat path (the hard-override/unparseable handling must reproduce the existing `_determine_enrollment_status` date branch exactly). Keep "SD74 must stay green" in the gate list; drop "must be regenerated."

**S2 — Pydantic dispatch: specify the exact integration, because `classify_field` will mis-handle the dict you propose.** Per [models.py:92-125](src/config/models.py), `classify_field` dispatches on first-matching key. The proposed `EnrollStatus` dict (`status_column` / `withdraw_date_column` / `active_values`) matches **none** of the known keys → it falls to the `logger.warning("Unrecognized…")` + raw passthrough at :124, i.e. it does NOT "fail loudly on unknown key" (it warns and passes), contradicting the plan (line 44/103). Worse, if anyone abbreviates to `{column: ...}` it would be mis-typed as `FieldTransform` (:120). No collision with `FieldNameConfig` (keys are space-form `"primary teacher flag"`) or `FieldIdRolePair` (`student_id_col`/`staff_id_col`) — those are distinct — so a dedicated branch is safe. **Required in spec:** add an explicit `if "active_values" in raw or "status_column" in raw or "withdraw_date_column" in raw:` branch returning a new strict `FieldEnrollStatus(BaseModel)` with `model_config = {"extra": "forbid"}` (so unknown keys raise), and a matching `get_raw_field_map` round-trip branch ([models.py:335](src/config/models.py)) so the transformer receives the raw dict. The plan's "fall back to raw-dict passthrough" escape hatch is fine *only* if it still raises on a malformed shape — but raw passthrough through the current `classify_field` does **not** raise, so the passthrough fallback as described fails the loud-failure requirement. Close that.

**S3 — `is_active_mask` definition is slightly circular and `Active`-leaky.** Spec line 99 defines the mask as `compute_enroll_status(...) ∈ active_values ∪ {"Active"}`. Hard-coding `∪ {"Active"}` means a district that deliberately removes `"Active"` from `active_values` would still admit Active rows. Simpler and self-consistent: define mask as `label != "Inactive"` (the plan even says this parenthetically). Make the code path *be* that, not the union. Minor, but it removes a foot-gun and the redundant set math.

### Sizing / completeness check

- **Slice 1 (predicate + models + students refactor + tests):** OK as one session *after* B1/B2/S2 are folded in. It touches `base.py`, `students.py`, `models.py`, one YAML comment, and ~3 test files — cohesive, lands complete. Do not let it land with the inverted tests deferred.
- **Slice 2 (publish set + filter classes/enrollments + guard):** OK. Correctly depends on Slice 1; the read sites are accurately enumerated. Confirmed the only demographic read sites are `classes.py:_create_homeroom_classes` ([classes.py:92](src/etl/transformers/classes.py)) and `enrollments.py:_load_student_demo` ([enrollments.py:54](src/etl/transformers/enrollments.py)); Family reads emergency contacts only ([family.py:12](src/etl/transformers/family.py)); StudentCourses reads `Student number` from history/selection independent of the roster ([student_courses.py:51](src/etl/transformers/student_courses.py)) — so the Family/StudentCourses out-of-scope reasoning is **correct**. Ordering claim (Students before Classes/Enrollments) confirmed: default `entity_order = list(mappings.keys())` ([pipeline.py:198](src/etl/pipeline.py)) and base YAML declares Students first ([myedbc_mapping.yaml:33](config/mappings/myedbc_mapping.yaml)); the empty-set guard correctly covers `mbp_core` (Students-only) and any reorder. One addition: also exercise the `_classinfo_coteacher_enrollments` path in the orphan regression — it emits **teacher** rows only (correctly out of scope), but a one-line test asserting it is unaffected prevents a future regression from quietly reintroducing student orphans there.
- **Slice 3 (docs):** OK, orchestrator-sized.

One cross-slice nit: Slice 2 sets `active_student_ids` from `Students.field_map["User ID"]` (line 112), but `apply_field_map` emits the output column literally named `User ID` from the same source. Add an assertion/test that the published set equals `set(result["User ID"])` (the plan lists this — good) and note the resolved column is `"student number"` per [myedbc_mapping.yaml:61](config/mappings/myedbc_mapping.yaml), so schedule `Student ID`→roster `Student Number` normalization is the right join. The "only 4 of ~2,300 non-matching" figure is unverifiable from the repo; keep it as motivation, not as a tested invariant.

### Gate / harness impact

- Add the three test files (B2) to the gate surface; the 80% coverage gate is unaffected (all touched modules are non-UI and already covered).
- `make validate-config` (all 8 configs) **must** include a malformed-`EnrollStatus` negative case once `FieldEnrollStatus` exists (S2) — currently no config exercises the dict form, so the strict validator would be untested by the existing gate. Add one bad-shape unit in `tests/test_config.py`.
- SD74 snapshot: keep in the gate as **expected-green** (S1), not a regeneration step.
- Harness: the new **referential-integrity** dimension in `ENGINEERING_STANDARDS.md` (Slice 3) is the right Stage-9 output and should be cited by the `architect-reviewer` at Verify. No new agent/STANDARD beyond that. The CLAUDE.md note (predicate centralized in `base.py`, configs filter against `context.active_student_ids`) is warranted and in scope.

Net: fold B1, B2, S2 into the Slice-1 spec and correct the SD74 framing (S1); the rest is polish. Architecture, slicing, and scope boundaries are right.
