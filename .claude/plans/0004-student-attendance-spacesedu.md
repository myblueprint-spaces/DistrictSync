# 0004 — SpacesEDU Attendance Import (StudentAttendance.csv) for SD51

- **Status:** ALL 3 SLICES DONE & verified on real SD51 data (uncommitted, branch `feat/sd51-student-attendance`). Real run = **79,303 rows across all 9 schools**, both bands. 8–12 built as **per-period PASS-THROUGH** (SpacesEDU weights each entry — K–7 absence 0.5, 8–12 absence 0.25, tardy 1, cap 1/day — and aggregates per-day itself, so NO AM/PM collapse is needed; the Enhanced `Period Id` turned out to be the rotating period block, not morning/afternoon, and no GDE carries a time-of-day anyway). **OPEN (with SpacesEDU, not a code blocker):** is the 0.25 weight applied by student *grade* or by *feed*? ~2,300 grade-8–12 students are at daily-attendance schools (2 entries/day) and would be under-counted if weighted 0.25 by grade.
- **Roadmap item:** docs/claugentic-ROADMAP.md → "StudentAttendance entity (SpacesEDU attendance sync, SD51)"
- **References:** `docs/claugentic-ARCHITECTURE_TREE.md` · `docs/developer/adding-transformer.md` · root docs `Attendance Import for SpacesEDU (BC & Aspen).docx`, `MyEducation BC General Data Extracts - Data Elements v1.8.pdf` · real GDEs in `data/input/` (PII — local only, never committed)
- **SpacesEDU contract (docx):** file ends in `StudentAttendance.csv`; case-sensitive headers; complete school-year set; "follows StudentPeriodAbsences (8–12) / StudentDailyAbsences (K–7)"; required cols School Number (blank OK for CSV), Absence Date (`DD-MMM-YYYY`, each row = ½ day, full day = 2 rows), Absence Category (others ignored), Student Number; SFTP file delivered **standalone, outside the zip**; **initial export reviewed by SpacesEDU** before go-live.

## Problem
SD51 runs DistrictSync for SpacesEDU rostering but has no attendance sync. DistrictSync emits no `StudentAttendance.csv`, and `uploader.py:173` zips all `*.csv` into one archive (would hide the attendance file from SpacesEDU's nightly standalone-file check).

## Goals / Non-goals
- **Goal:** Opt-in `StudentAttendance` entity → `StudentAttendance.csv` for SD51, **K–12**, from **Student Period Absences – Enhanced** (8–12) + **Student Daily Absences** (K–7); 28-column output (4 populated, 24 blank); delivered **standalone over SFTP** alongside the unchanged advanced-CSV zip.
- **Non-goal:** Enable for any other district (defined in base, enabled only in SD51 → SD74 snapshot byte-identical).
- **Non-goal:** 2-way sync; manual-import UX.

## Real GDE data findings (from `data/input/`, PII-masked)
- **Both attendance GDEs are HEADERLESS, comma-separated** → require a `headers` block per file (same mechanism as SD40's headerless schedule; EntityConfig `headers`).
- **Student Period Absences (base) — 45,979 rows — is PER-PERIOD** (1–6 rows/student/day). `Absence Category` is a rich raw vocabulary (`A-E`,`A`,`L`,`L-E`,`AD`,`AL` … plus non-accepted `OffSite`/`D`/`AUTH`/`ISS`/…). **Base export lacks an AM/PM signal** (no Period ID; `Master Time Table ID` blank → no reliable schedule join). **Decision: SD51 re-exports the *Enhanced* variant** (`Period ID` = Morning/Afternoon) → `StudentPeriodAbsencesEnhanced.txt`.
- **Student Daily Absences (K–7) — 20,198 rows:** `Absent Code AM` ∈ {`A` absent, `T` tardy, blank}; `Absent Code PM` entirely blank; `Authorized AM` ∈ {Y,N}; `Reason Code AM` = free text; `Portion Absent` ∈ {1.0, 0.5, 0.25, 0.75, 0.0}. The AM/PM-sub-allocation model the PDF implies is NOT how this district records — it's a single code + a day-portion fraction.

## Output format (locked by the SpacesEDU sample)
- **28 columns, exact case-sensitive header order from the sample**; only **School Number, Absence Date, Absence Category, Student Number** populated; the other 24 emitted **blank** (`{value: ""}`).
- `Absence Date` → **`DD-MMM-YYYY`** (new `format_dd_mmm_yyyy` transform).
- **No `drop_duplicates`** — a full-day absence is intentionally two identical rows (half-day = row multiplicity; there is no AM/PM column). Quality report treats StudentAttendance duplicates as expected.

## Transformation rules (DERIVED from the docx accepted-value set; config-tunable as a safety net)
**Docx accepted categories:** Absence `{A, AD, A-E, A-E OffSite}` · Tardy `{AL, AL-E, L, L AUTH, L-E}`; others ignored. Each output row = a half-day (full day → 2 rows).

**8–12 (Enhanced Period Absences) — no category derivation.** The GDE `Absence Category` column already carries these exact codes → **pass through as-is** (SpacesEDU ignores non-accepted `OffSite`/`ISS`/`D`/…). Work = the **half-day collapse**: group by `(Student Number, Absence Date, Period ID)` → one row per half-day (full day present in both Morning & Afternoon → 2 rows); if a half has multiple categories pick by priority **absent (`A*`) > late (`L*`) > other**. *(Period ID is the morning/afternoon signal the docx half-day model requires; confirm its literal values on the Enhanced re-export.)*

**K–7 (Daily Absences) — derivation pinned by the docx accepted set:**
| Absent Code | Authorized | → Category |
|---|---|---|
| `A` | `N` | **A** (absent, unexcused) |
| `A` | `Y` | **A-E** (authorized absence → A-E; **`A AUTH` is not an accepted absence value, `A-E` is**) |
| `T` | `N` | **L** (tardy = late) |
| `T` | `Y` | **L-E** (excused late) |
| blank | — | *drop* |

Row count from `Portion Absent` (docx: full day = 2 rows): `1.0` → **2 rows**; any partial absence (`0 < p < 1`) → **1 row**; tardy (`T`) → **1 row**; blank-code → drop.

**Both bands union** into one frame; **no active-roster filter** (whole-year data incl. withdrawn).

**Runtime-configurable (not Python constants — plan-review fix):** the Period-ID Morning/Afternoon value lists, the K–7 `(Absent Code, Authorized) → Category` map, and the `Portion Absent → row count` thresholds all live in a `global_config.attendance` block **read at runtime**; an **unmapped category/code fails loud** (raises, per *fail-loudly*) rather than silently dropping — so SpacesEDU's review tunes config, never code.

## Verified code patterns (file:line)
- Entity transformer = 3-arg `transform(df, mapping, context)` (`course_info.py:19`); pipeline calls facade `DataTransformer.transform(df, mapping, entity, raw_data, global_config)` (`transformer.py:105`) which injects `context.raw_data`/`global_config`. Tests use the facade. Second GDE read via `context.raw_data[...]`.
- `enabled_entities` gates (`pipeline.py:202-205`); deep-merge **replaces** lists → SD51 lists full set + `StudentAttendance`.
- **Skip-on-empty-primary** (`pipeline.py:219-224`): order source_files so the always-present band is primary (SD51 K–12 → both present).
- `apply_field_map` (`base.py:679-733`): bare string=direct col; `{value:""}`=blank; `{column,transform}` runs an `ALLOWED_TRANSFORMS` method (`base.py:28`). Column order = field_map key order. Output filename = `{entity}.csv` (`loader.py`). Headerless `headers` injected by extractor.
- No Pydantic/loader change (entity map open). Registry `registry.py:38-46`.

## Affected files (touchpoints)
- **new** `src/etl/transformers/student_attendance.py` — `StudentAttendanceTransformer` (Period-Enhanced collapse + Daily derivation + union; reads both files from `context.raw_data`).
- `src/etl/transformers/registry.py` — import + registry entry.
- `src/etl/transformers/base.py:28` + method — `format_dd_mmm_yyyy` transform.
- **No `src/etl/column_names.py` change** (plan-review fix) — attendance source columns are single-use, not shared join keys; resolve them via the per-entity `headers:` block + `field_map` (column_names.py stays for shared structural keys only; keeps Configurable-Columns).
- `config/mappings/myedbc_mapping.yaml` — `StudentAttendance` entity (source_files Enhanced-Period + Daily; **headers** blocks for both headerless files; full 28-col field_map; the `attendance` derivation block in `global_config`); **NOT** in base `enabled_entities`.
- `config/mappings/sd51myedbc_mapping.yaml` — add `global_config.enabled_entities` with the **full list** (Students, Staff, Family, Classes, Enrollments, StudentAttendance) — SD51 has **none today** and deep-merge replaces, so the full list is required, not a `+=`; source filenames `StudentPeriodAbsencesEnhanced.txt`, `StudentDailyAbsences.txt`.
- `src/quality/report.py` — **explicit** `key_map["StudentAttendance"]` skip entry (duplicates legitimate); don't rely on the fallback heuristic.
- `src/etl/pipeline.py` `_emit_run_log` (~100-106) — surface StudentAttendance count.
- `src/sftp/uploader.py:173-193` — deliver `StudentAttendance.csv` standalone (excluded from zip), guarded by presence.
- `GDE2Acsv.spec` + `Makefile` build-win + `.github/workflows/release.yml` (3 jobs) — `--hidden-import` (defensive; `collect_submodules('src')` already covers it).
- **new** `tests/test_transform_student_attendance.py` + `tests/conftest.py` synthetic fixtures (Enhanced-Period collapse + Daily portion/derivation). **No real PII in tests.**
- `docs/claugentic-ARCHITECTURE_TREE.md` — index the new transformer.

## Risks & mitigations
- **SD74 snapshot drift** → not enabled for SD74; `tests/test_regression_sd74.py` byte-identical (verify). No snapshot config edit.
- **Other districts' SFTP** → standalone delivery guarded by `StudentAttendance.csv` presence.
- **Derivation correctness (K–7 category/portion, 8–12 tie-break, Period ID values)** → documented + **config-tunable**; validated against this real data + SpacesEDU's initial-export review before go-live.
- **Enhanced re-export dependency** → SD51 must export `StudentPeriodAbsencesEnhanced.txt`; if the primary band file is missing, pipeline skips the entity (fail-loud warning) — order Daily/Period so a present file is primary.
- **PII** → real GDEs analysed locally only; fixtures synthetic; nothing real committed/logged.

## Test strategy
- Unit (via facade): Enhanced-Period fixture → half-day collapse (full day→2 rows, AM-only→1), tie-break, `DD-MMM-YYYY`; Daily fixture → category derivation table + portion→row-count + tardy=1; union; empty-band skip; no-dedup preserved.
- `make validate-config` green (all 9 + headers blocks); SD51 emits 6 entities; SD74/others unchanged + regression byte-identical.
- Uploader: `StudentAttendance.csv` standalone + excluded from zip; absent-case unchanged.

## Decomposition (slices) — each lands complete & DEPLOYABLE, no debt  _(re-ordered: plan-review put SFTP first; data availability puts K–7 before 8–12)_
- [x] **Slice 1 — SFTP standalone delivery.** DONE on `feat/sd51-student-attendance` (not committed): `uploader.upload_csvs` excludes `StudentAttendance.csv` from the zip + standalone `put`, guarded by presence; no-attendance path byte-identical; 3 tests; full suite green; ruff/mypy/bandit/tree clean.
- [x] **Slice 2 — Entity scaffold + K–7 (Daily Absences) + SD51 enable.** DONE & verified (uncommitted): new transformer, `format_dd_mmm_yyyy`, `headers` + `global_config.attendance.daily` config (category map + portion→rows, fail-loud), registry, base entity, SD51 full `enabled_entities`, quality key, run-log, packaging, tree, DECISIONS entry. 23 new tests; full suite 828 green; SD74 byte-identical; live SD51 run emits a valid K–7 `StudentAttendance.csv` (full-day→2 rows `A`, excused-tardy→1 row `L-E`).
- [x] **Slice 3 — 8–12 (Period Absences), per-period PASS-THROUGH.** DONE & verified. Used the standard headerless `StudentPeriodAbsences.txt` (NOT Enhanced — `Period Id` is the rotating period block, not AM/PM; and SpacesEDU's per-entry weighting makes any collapse unnecessary). One output row per period absence (category pass-through; SpacesEDU ignores non-accepted), unioned with K–7 daily; `attendance.period` config block. 18 new tests; full suite 846 green; SD74 byte-identical; real SD51 run = 79,303 rows across all 9 schools.
- **At Land:** `docs/claugentic-DECISIONS.md` line (SFTP delivery-shape = partner-contract change) + update `CLAUDE.md`'s SFTP description (`upload_csvs` = zip + standalone sidecar).

## Validation items (with SpacesEDU / SD51, not code blockers)
1. SD51 re-exports **Student Period Absences – Enhanced** (`StudentPeriodAbsencesEnhanced.txt`); confirm `Period ID` values.
2. SpacesEDU reviews an initial export to confirm: 8–12 half-day collapse + category set; K–7 category derivation + portion→row-count + tardy handling.
3. Confirmed: Student Number = MyEd pupil # (matches Students.csv); SD51 single K–12 job (both files present).

---

## Review  _(plan-reviewer, Stage 3)_

**Reviewer family:** Opus 4.x (same vendor as a likely Opus builder → treat as a *reduction* of shared-blind-spot risk, not an independent oracle).

**Verdict:** CHANGES REQUIRED

### Code-pattern claims — spot-checked against source
Mostly accurate; verified:
- 3-arg `transform(df, mapping, context)` + facade injecting `context.raw_data`/`global_config` — confirmed (`course_info.py:19`, `transformer.py:105-116`). ✔
- `enabled_entities` gate + list-replace deep-merge — confirmed (`pipeline.py:202-205`); base lists only the 5 rostering entities (`myedbc_mapping.yaml:33-38`). ✔ **But note:** SD51 currently has **no** `enabled_entities` key at all — it inherits the base 5. So the SD51 edit is **not** an "`+=`" (line 48); the implementer must paste the **full 6-entity list** into SD51 or the rostering entities vanish. State this explicitly in the spec.
- `apply_field_map` forms (`base.py:679-733`): `{value:""}` blanks ✔; `{column,transform}` runs an `ALLOWED_TRANSFORMS` method via `series.apply(func)` — i.e. **scalar→scalar per cell** ✔ (`format_dd_mmm_yyyy` fits). `ALLOWED_TRANSFORMS` is at `base.py:28` ✔ — new transform **must** be added there or `apply_field_map` raises.
- Headerless `headers` block — confirmed as a **per-entity** `EntityConfig.headers` field (`models.py:162`; live example `sd40myedbc_mapping.yaml:41-62`). Plan's term "headers" is right; the dev doc's `file_headers:` (adding-transformer.md:238) is stale — don't follow it.
- Skip-on-empty-primary (`pipeline.py:219-224`) ✔; output filename `{entity}.csv` (`loader.py:104`) ✔; loader **raises** if any `field_order` column is absent (`loader.py:101-103`), so all 28 field_map keys must materialize — the 24 `{value:""}` columns cover this. ✔
- "`collect_submodules('src')` already covers it" — **only true for `GDE2Acsv.spec`**. The Makefile `build-win` (`Makefile:62-72`) and **all three** `release.yml` jobs (`release.yml:57-66,97-107,136-146`) enumerate an **explicit `--hidden-import` per transformer** (course_info, student_courses, …). release.yml also passes `--collect-submodules=src` (belt-and-suspenders), but the Makefile does not. Treat the explicit line as **required for convention-consistency**, not "defensive."

### Required changes
1. **Re-order the slices so SD51 is never enabled in production without standalone SFTP delivery (highest priority).** As drawn, Slice 1 adds `StudentAttendance` to SD51's `enabled_entities` while the SFTP fix is deferred to Slice 3. The moment Slice 1 lands, any SD51 `--sftp` run will zip `StudentAttendance.csv` **into the rostering bundle** (`uploader.py:173` globs `*.csv`) — which (a) violates the docx contract ("delivered standalone, outside the zip"; SpacesEDU's nightly check won't see it) and (b) **injects a foreign file into the previously-correct advanced-CSV zip**, risking the rostering importer. That is a deployed half-done/debt state — it fails the "land complete" gate. Fix: either (a) make the SFTP standalone-delivery change **Slice 1** (it's small and self-contained — it can land before any attendance generation exists, keyed on `StudentAttendance.csv` presence, no-op until the entity is enabled), or (b) **do not enable `StudentAttendance` in SD51 until the SFTP slice has landed** — i.e. keep the entity base-only/unenabled through Slices 1-2 and combine "SD51 enable + standalone delivery" into the final slice. Option (a) is cleaner. The current ordering is the plan's main defect.
2. **Decouple the un-validated grouping/derivation rules from the shippable transformer (de-risk Slice 1's open questions).** The plan flags two unconfirmed facts but bakes them into hardcoded transform logic: (i) the literal domain of `Period ID` on the *Enhanced* re-export (the 8-12 `(Student, Date, Period ID)` collapse silently mis-collapses if `Period ID` is not a clean two-value Morning/Afternoon partition — e.g. period-numbered values would emit up to 6 rows/day, not 2), and (ii) the K-7 `Portion Absent → round(p×2)` row-count + category-derivation table. The plan says "config-tunable," but tunability is only real if the **thresholds, the AM/PM Period-ID value list, and the category-derivation map all live in `global_config`/`field_map` and are read at runtime** — not as Python constants the SpacesEDU review can't change without a code release. Make the spec require: Period-ID Morning/Afternoon value lists, the K-7 code+authorized→category table, and the portion→row-count rule **all config-resident**, with a **fail-loud warning** when an unmapped `Period ID` / `Absent Code` value is encountered (per CLAUDE.md "fail loudly" — do not silently drop or mis-bucket). This also keeps Slice 1 truly complete: the unverified value just becomes a config default, not a code-change-on-confirmation debt.
3. **Honor "Configurable Columns" for the headerless source columns.** The plan plans `column_names.py` constants for absence-GDE columns "config-overridable." But headerless files get their names from the per-file `headers:` block, and `apply_field_map`/direct reads must resolve those names from `field_map`/config, **never** from module constants. Constants in `column_names.py` are only sanctioned for *shared structural join keys* — single-use attendance columns belong in the config `field_map` + the `headers` list, not as new `column_names.py` literals (that would repeat the `student_courses.py` anti-pattern the plan says it wants to avoid). Spec: derive every source column from the entity's `field_map`/`headers`; add to `column_names.py` **only** a genuinely shared key (likely none here — `Student Number`/`School Number` are output names).
4. **Correct the quality-report touchpoint.** `report._check_duplicates` (`report.py:64-81`) has no `StudentAttendance` key; the unknown-entity fallback keys on columns ending `" ID"` or `" Code"` — StudentAttendance's populated columns are `School Number` / `Student Number` / `Absence Date` / `Absence Category`, **none** of which match, so the heuristic already finds no keys and runs no dup check. So "exclude from duplicate check" is largely a no-op *today*, but it is **fragile** (any future renamed column ending in " ID" would trip it). Add an **explicit `StudentAttendance: []` (or sentinel)** entry to `key_map` so the intentional-duplicates contract is encoded, not accidental. (Add a brief test asserting no duplicate warning on a full-day double-row frame.)
5. **Make the run-log + packaging edits explicit Slice-1 line items, not afterthoughts.** `_emit_run_log` (`pipeline.py:96-111`) hardcodes the seven entity keys — add `"StudentAttendance": len(outputs.get("StudentAttendance", []))`. Add `src.etl.transformers.student_attendance` to the Makefile `build-win` list **and all three** release.yml job lists (convention; release.yml's `--collect-submodules` covers runtime, but the explicit line is the established pattern and avoids a reviewer flag). The `.spec` needs no edit (it uses `collect_submodules`).

### Sizing / completeness check (per slice)
- **Slice 1 (scaffold + 8-12)** — **Split / re-scope needed.** Two problems: (a) it is *not* independently shippable to SD51 production because it enables the entity before standalone SFTP exists (Required change 1); (b) it bundles a lot — new transformer + new transform + headers + registry + base entity + SD51 enable + quality + run-log + 3 packaging files + tests + tree. That's wide but each piece is mechanical and the transformer is the only real logic, so it **fits one session** *if* the SFTP ordering is fixed and the derivation rules are config-resident (so no "confirm-then-edit-code" tail). Recommended re-slice: **Slice 1 = standalone SFTP delivery** (smallest, self-contained, no-op until enabled); **Slice 2 = entity scaffold + 8-12 Enhanced-Period generation + SD51 enable** (now safe to deploy, because delivery already handles the file); **Slice 3 = K-7 Daily union**. This keeps every landed slice deployable.
- **Slice 2 (K-7 Daily)** — OK *as logic*, contingent on Required change 2 (config-resident derivation + fail-loud on unmapped codes). Union into one frame is sound; "no active-roster filter" is correct here (whole-year incl. withdrawn) and does not touch the zero-orphan invariant (StudentAttendance is not Enrollments/Classes). Confirm the spec states the union dedup rule explicitly (the plan correctly wants **no** `drop_duplicates`).
- **Slice 3 (SFTP standalone)** — OK as a unit, but **should be first** (see above). Acceptance must include: attendance file `put` standalone, **excluded** from the zip, the rostering zip byte-unchanged when StudentAttendance is absent, and the existing exit-code-3 failure semantics preserved.

### Harness impact
- **No new STANDARD or agent required.** This is a `feature` on the existing `maintainability-structure` / `data-and-persistence` / `reliability-resilience` / `observability-ops` dimensions; the SFTP change touches the **API/interface contract** (output-file delivery shape) — note it in the Stage-8 `claugentic-DECISIONS.md` (standalone-vs-zip delivery is a partner-contract decision worth recording).
- **Doc fixes to fold in (Stage 6/9):** (a) `docs/developer/adding-transformer.md:238` says `file_headers:` — the live field is per-entity `headers:`; correct it so the next transformer author isn't misled. (b) Add the `docs/claugentic-ROADMAP.md` item the plan references at line 3 (it is not present today) before Land, or drop the reference.
- **ARCHITECTURE_TREE** update for `student_attendance.py` is listed ✔ (tree-check hook will enforce it).
- A second SFTP-delivery-shape (zip + standalone sidecar) is a new wrinkle in `uploader.upload_csvs` — worth a one-line gotcha in `CLAUDE.md`'s SFTP section once it lands (Stage 9 candidate), since the "zips all `*.csv`" description is currently absolute.
