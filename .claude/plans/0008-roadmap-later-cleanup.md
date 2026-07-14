# 0008 — ROADMAP `## Later` cleanup (2 bugs + stale-output prune)

- **Status:** Spec'd (plan-review CHANGES REQUIRED + yagni CUT folded → Resolution + Spec below) — **awaiting user approval (Stage 5)**. Two material changes from draft: Item 2 cut to a doc decision; Item 3 → warn-only (auto-prune deferred).
- **Branch:** `fix/roadmap-later-cleanup` (stacked on `feat/unify-conversion-pipeline` tip `f2e7c8d`, which has `origin/main`/PR #31 merged in). Base for its eventual PR = `feat/unify-conversion-pipeline`. **No CI on that PR (base ≠ main)** → gate every slice with a **local full-suite run**.
- **Roadmap items:** the three `## Later` entries the user selected — (1) period-only attendance silently produces no output `[bug]`; (2) `append_year_to_id` not row-resilient `[bug]`; (3) stale unmanaged output files `[gap / design decision]`.
- **References:** `src/etl/pipeline.py` (`run_transform` skip; `run_pipeline` save_all call) · `src/etl/transformers/base.py` (`apply_field_map`/`_apply_transform_resilient`) · `src/etl/loader.py` (`save_all`) · `config/mappings/myedbc_mapping.yaml` · `docs/claugentic-DECISIONS.md` · `docs/claugentic-ARCHITECTURE_TREE.md` · `docs/claugentic-ROADMAP.md`.

## Problem

Three items deferred to `## Later`, all small, all in the ETL core:

1. **Period-only attendance silently produces no output `[bug]`.** `run_transform` skips an entity when its **positional-first** source frame is empty (`pipeline.py:140-145`: `primary_source = source_files[0]; if primary_df.empty: skip`). But `StudentAttendanceTransformer` resolves its daily/period **bands by ROLE** (`get_source_file(..., "daily_absences"/"period_absences")`), order-independent. A **period-only** district (`sd51attendance`: daily file empty/absent, period file present) lists `daily_absences` first, so `source_files[0]` is the empty daily file → the entity is skipped → **no `StudentAttendance.csv`, run exits 0**. A supported scenario silently yields nothing.

2. **`append_year_to_id` is not row-resilient `[bug, low-severity]`.** In `apply_field_map` the `append_year_to_id` branch (`base.py:778-785`) uses `working.apply(generate_class_id, axis=1)`; if `generate_class_id` raised on one row the **whole column** would blank (recorded as a *column-level* `data_error` since 0006 — loud, not silent). The `transform:` path is already per-row resilient (`_apply_transform_resilient`, `base.py:830`); this branch is the documented exception (`base.py:752`). **Honest severity note:** `generate_class_id` (`base.py:545-549`) is 4 trivial lines (`row.get` + f-string) with no parse/IO — it essentially **cannot raise** today, so this is a **consistency / defense-in-depth** fix, not a live data-loss bug.

3. **Stale unmanaged output files `[gap]`.** `DataLoader.save_all` only manages files named in this run's `outputs`. If a prior run emitted (say) `StudentAttendance.csv` and the config later drops that entity from `enabled_entities`, the **stale CSV persists** in the output dir and ships in the next SFTP zip — delivering an out-of-date entity to SpacesEDU. Discovered while planning 0007; the atomic-commit fix neither caused nor addresses it.

## Goals
- A period-only attendance district produces its `StudentAttendance.csv` (entity skipped only when it has **no** usable source at all).
- `append_year_to_id` blanks only a failing **row's** cell (consistency with the `transform:` path), recorded the same loud way.
- The output directory never ships a **stale** entity CSV from a prior, now-disabled entity (per the chosen Decision below).
- Each bug ships a **reproduce-first** regression test (`bug` discipline): failing test against current code → fix → green.

## Non-goals (YAGNI)
- **No SD74 behavior change** — SD74 enables none of the touched paths; **snapshot stays byte-identical.**
- Don't rework the positional-`source_files[0]` *primary* concept generally — only the **skip condition** changes (the primary is still passed to `transform`; transformers already tolerate an empty primary).
- Don't touch the no-usable-input guard (0006) or the atomic commit (0007) semantics.
- No new config, no new exit code, no entity-registry refactor.

## Approach (grounded in the findings)

### Item 1 — skip only when ALL sources are empty (`pipeline.py` `run_transform`)
Replace the positional-primary emptiness skip with an **all-sources-empty** skip:
```python
source_frames = [raw_data.get(sf, pd.DataFrame()) for sf in source_files]
if all(df.empty for df in source_frames):
    logger.warning(f"All source files {source_files} are empty for '{entity_name}'; skipping.")
    continue
primary_df = source_frames[0]   # may be empty for a role-resolved entity (period-only attendance)
transformed = transformer.transform(primary_df, entity_cfg, entity_name, raw_data, global_config)
```
**Why safe (verified):** every multi-source transformer tolerates an empty primary — `EnrollmentTransformer.transform` returns `pd.DataFrame()` immediately when the schedule is empty (`enrollments.py:22-24`); `ClassTransformer` returns empty when the schedule is empty (`classes.py:181-182`) and resolves secondaries by role; `StudentAttendanceTransformer` resolves both bands by role. Anything that legitimately can't produce output returns empty → caught by the existing `if transformed.empty: skip` guard (`pipeline.py:149-151`). So the change converts "period-only attendance is skipped" into "it runs", without letting a genuinely-empty entity emit a file.

### Item 2 — make `append_year_to_id` per-row resilient (`base.py` `apply_field_map`)
The `transform:` path applies `func(value)` over a Series; the `append_year_to_id` path applies `generate_class_id(row)` over rows (`axis=1`). Mirror the resilience with a small per-row-safe application: iterate the rows, `try generate_class_id(row)` → value, `except` → `pd.NA` for that cell + accumulate into `context.data_errors` (reuse `_record_data_error`), log ERROR. Keep the column-level `except` (outer `try`) as the structural-failure backstop. **Minimal** — a few lines; no new public surface.

### Item 3 — prune stale entity CSVs (DESIGN DECISION below)
`run_pipeline` knows the full configured entity set (`mappings.keys()`) and the emitted set (`outputs.keys()`); the output dir holds only entity CSVs (zips → tmpdir, logs → logger, `.tmp_*`/`.bak_*` auto-cleaned). So removing `{entity}.csv` for entities **in the known set but not emitted this run** is safe (only ever touches known entity filenames). *Where/how* is the judgment call:

## Design decision for the user — stale-output prune

### Decision — where to prune, and how safe/atomic
- **Option A — `DataLoader.prune_stale(keep_entities, known_entities)`, called by `run_pipeline` after a successful `save_all`. [RECOMMENDED]** A new loader method deletes `{e}.csv` for `e in known_entities - keep_entities` that exist on disk; `run_pipeline` passes `keep = set(outputs)`, `known = set(mappings)`. **Simple (~10 lines + test), safe** (only known entity filenames, never unmanaged files), loud (logs each prune). Not part of the atomic commit, but **benign**: the new roster is already committed; a failed prune leaves an *extra stale file* (logged), never a torn/missing roster. The loader stays the output-dir authority.
- **Option B — Prune *inside* `save_all`'s atomic commit.** `save_all` gains a `known_entities` param; stale files are backed up + removed within the same `.bak_<ts>/` transaction (rolled back on failure). **Most correct** (atomic, dir always exactly the emitted set) but **re-touches the just-landed 0007 commit logic** (more risk/complexity) for an edge case. *Why not (default):* cost > benefit; the prune is benign out-of-band.
- **Option C — Warn only.** Log a WARNING listing stale entity CSVs; don't delete. Lowest risk, but **doesn't fix delivery** (stale file still ships). *Why not:* the user asked to address the gap, not just flag it.

**Plan recommends Option A.** (Pick A/B/C at spec approval.)

## Decomposition into slices (3 commits on `fix/roadmap-later-cleanup`)
- **Slice 1 — Item 1 (attendance skip).** `pipeline.py` + tests. `bug`, reproduce-first.
- **Slice 2 — Item 2 (`append_year_to_id` resilience).** `base.py` + tests. `bug`, reproduce-first.
- **Slice 3 — Item 3 (stale-output prune).** Per chosen Decision; `loader.py` (+`pipeline.py` call) + tests.
Each lands complete (code + tests + docs), each gated by a **local full suite + SD74**. Independent; order 1→2→3.

## In-scope standards dimensions
- **reliability-resilience** — a supported attendance run no longer silently yields nothing; per-row resilience consistency; output dir self-heals stale entities.
- **data-and-persistence (integrity)** — no stale entity CSV ships; emitted set matches the dir.
- **maintainability-structure** — skip rule reads honestly ("all sources empty"); resilience pattern applied consistently.
- **testing** — reproduce-first per bug; SD74 byte-identical guard.

## Risks & mitigations
- **Item 1 — a multi-source entity with empty primary but a populated secondary now calls `transform`.** Verified all such transformers return empty on empty primary → caught by `transformed.empty`. *Mitigation:* a regression test that an entity whose transform yields empty is still skipped (no file, no crash); SD74 byte-identical.
- **Item 1 — false un-skip emitting an empty file.** The `transformed.empty` guard remains the final net; test it still fires.
- **Item 2 — over-engineering an impossible failure.** Acknowledged low severity; keep the change minimal + a test that injects a raising row to prove per-cell isolation (forces the path that can't otherwise occur). yagni to confirm it's worth doing vs. documenting.
- **Item 3 — deleting a file we shouldn't.** Prune is allow-listed to `known_entities` filenames only; output dir verified to hold only entity CSVs; each delete logged. Test: a stale `Classes.csv` is pruned, an unrelated file is untouched, a current entity is kept.
- **Stacked-PR has no CI** → run the full suite + SD74 locally before each commit (recorded in the branch note).

## Test strategy
1. **Item 1:** reproduce-first — a synthetic `sd51attendance`-shaped run with daily empty + period present → today the entity is skipped (no `StudentAttendance` in outputs); after fix it IS produced. Plus: all-empty sources → still skipped; a non-attendance entity whose transform returns empty → still skipped (no crash).
2. **Item 2:** reproduce-first — monkeypatch/inject a `generate_class_id` that raises on one row in an `append_year_to_id` field → today the whole column blanks (one column-level `data_error`); after fix only that row's cell is `pd.NA`, other rows keep their Class ID, and a per-row `data_error` is recorded. Valid input unchanged.
3. **Item 3:** pre-seed the output dir with a stale `Classes.csv` (+ an unrelated `notes.txt`), run a pipeline whose `outputs` excludes `Classes` → stale `Classes.csv` removed, `notes.txt` untouched, emitted CSVs present. (Per chosen Decision.)
4. **SD74 snapshot byte-identical** (none of the three paths fire on SD74).
5. **Gates (local):** full `pytest --cov` (80%), `ruff check`+`format`, `mypy src/ --exclude 'src/ui'`, `bandit`, config-validation, tree-check. (CI does not run on this stacked PR.)

## Approval triad (plain English)
- **What this builds:** three small cleanups from the backlog — (1) an attendance run that only has the **period** file (no daily) now actually produces `StudentAttendance.csv` instead of silently producing nothing; (2) if one row ever broke the class-ID calculation, only **that row's** Class ID would blank (not the whole column) — consistency with how other fields already behave; (3) when you disable an entity in a district's config, its **old CSV no longer lingers** in the output folder to be re-delivered.
- **What "done" means for you:** normal runs are unchanged (SD74 proves it byte-for-byte). Each bug has a test that fails on today's code and passes after the fix. Because this PR stacks on #32, **CI won't run on it** — I gate every slice with the full suite locally.
- **What you're accepting:** (1) the skip rule changes from "first source empty" to "all sources empty" (safe — every transformer already handles an empty primary). (2) a tiny consistency fix for a failure that basically can't happen today (defense-in-depth — say the word if you'd rather just document it). (3) for the stale-file cleanup you're choosing **Option A** (prune after a successful write — simple & safe) unless you pick B (atomic, more complex) or C (warn only).

---

## Review  _(plan-reviewer — Stage 3)_

> **Same-model honesty tag:** I am running as **Opus 4.x** (clean-context, separate `plan-reviewer` role). If the builder of this plan was also an Opus-4.x model, treat this as a **same-model review** — independence is of *role + context*, not of model, so shared blind spots are not excluded.

**Verdict: CHANGES REQUIRED** — one real correctness bug in Item 3's allow-list (#1 below), plus tightening on Items 1/2. Items 1 and 2 are sound in approach; the plan's factual claims check out against the code.

### Verified against the real code (claims hold)
- **Item 1 bug is real.** `pipeline.py:140-145` skips on `source_files[0]` empty; `StudentAttendanceTransformer.transform` (student_attendance.py:76-96) ignores the positional `df` entirely and resolves both bands by role from `context.raw_data`. Base `StudentAttendance.source_files: {}` (myedbc_mapping.yaml:300); `sd51myedbc` declares `daily_absences` first (sd51myedbc_mapping.yaml:48) → a period-only district has the empty daily file as `source_files[0]` → entity skipped. Confirmed.
- **Empty-primary tolerance verified** for the three named transformers: `EnrollmentTransformer` returns `pd.DataFrame()` on empty schedule (enrollments.py:23-24); `ClassTransformer._create_subject_classes` returns on empty schedule (classes.py:181-182) and resolves secondaries by role; `StudentAttendanceTransformer` resolves both bands by role and each band guards `.empty`. The `transformed.empty` net at pipeline.py:149-151 is the correct backstop.
- **Item 2 facts hold.** `append_year_to_id` uses `working.apply(generate_class_id, axis=1)` (base.py:778-785); `generate_class_id` (base.py:545-549) is `row.get` + an f-string — it genuinely cannot raise today. `_apply_transform_resilient` (base.py:830-862) iterates **per-value over a Series**, and the plan correctly states it cannot be reused as-is for the **per-row (`axis=1`)** branch. Good — the plan does not over-claim reuse.

### Required changes (numbered, actionable)

1. **[BLOCKER — Item 3] `known_entities = mappings.keys()` is the WRONG allow-list and will delete files this tool legitimately doesn't manage.** `config.to_raw_dict()` builds `mappings` from **all** entities in the merged config (models.py:339-347 iterates `self.mappings.items()`), which for any district inheriting `_base: myedbc` includes the full base set — **`CourseInfo` and `StudentCourses`** — even though `sd51myedbc`/`sd40myedbc`/etc. **never enable them** (enabled set is sd51myedbc_mapping.yaml:15-21). So `known - keep` would include `CourseInfo`/`StudentCourses`, and the prune would **delete a `CourseInfo.csv`/`StudentCourses.csv` that a prior `mbp_core`/`mbp_all` run wrote into the same output dir** — exactly the cross-tool data-loss the plan claims to avoid. **Fix:** the allow-list must be the **enabled** entity set, not `mappings.keys()`. Compute `known = set(global_config["enabled_entities"]) or set(mappings)` (mirror `run_transform`'s own enabled-filter at pipeline.py:123-126: empty/absent `enabled_entities` = all). Then `prune = known - keep` only ever names entities **this config is configured to emit** but didn't this run. Update the Approach text (line 48), Option A (line 53: "`known = set(mappings)`"), and the risk note (line 75) accordingly.

2. **[Item 3] State the cross-config-sharing assumption explicitly, or scope the prune to entities this config can emit.** Even with the enabled-set fix (#1), the design assumes one output dir per config. If a district points `mbponly` and `sd51myedbc` at the **same** output dir (both are real configs here), pruning is still correct *per the fix above* because each only prunes within its own enabled set — but make that invariant explicit in the plan ("prune only removes `{enabled} - {emitted}`; it never touches an entity outside this run's config's `enabled_entities`") so the spec author and reviewer can verify it. Add a test for it: an `mbponly`-written `CourseInfo.csv` is **untouched** by a subsequent `sd51myedbc` run into the same dir.

3. **[Item 3] Justify the output-dir-purity assumption with a check, not just prose.** The plan asserts "the output dir holds only entity CSVs (zips → tmpdir, logs → logger, `.tmp_*`/`.bak_*` auto-cleaned)" (line 48). Verified: `_sftp_upload` zips elsewhere, `.tmp_*`/`.bak_*` are rmtree'd in `save_all`'s `finally`. Good — but the prune still only ever `unlink`s `{enabled-but-unemitted}.csv` by exact name, so even a stray non-entity file is safe by construction. Keep the existing risk-test (line 75: unrelated `notes.txt` untouched) — that test is the real guarantee, so it must assert the prune **only** deletes the specific stale entity CSV and nothing else (including no other entity's CSV).

4. **[Item 2 — downgrade, YAGNI] Strongly consider demoting this from a code change to a one-line guard + doc note.** The plan itself concedes `generate_class_id` "essentially cannot raise today" (line 14). Adding a bespoke per-row try/except loop that mirrors `_apply_transform_resilient` (but can't reuse it, since one is per-value-over-Series and the other per-row-over-rows) introduces a **second, near-duplicate resilience loop** to defend a failure mode that cannot occur — that is the DRY/YAGNI smell the harness's `yagni-sentinel` exists to catch. Two acceptable resolutions: **(a)** drop the code change; record a DECISIONS line that the `append_year_to_id` branch is column-level-resilient (loud, not silent) and that's sufficient because `generate_class_id` is non-raising by construction — this is honest and costs nothing; or **(b)** if you keep it for consistency, do NOT add a parallel loop — extract a tiny shared `_apply_rowwise_resilient(working, fn, ...)` so there is **one** resilience helper, and refactor the `transform:` path's Series case to call a `_apply_perval_resilient` sibling, both recording via `_record_data_error`. Pick (a) unless the user explicitly wants the consistency. Either way, the "inject a raising row" test (line 80) is legitimate as a guard-rail. State the chosen option in the plan before spec.

5. **[Item 1 — confirm no test/guard regression] The change is safe w.r.t. the 0006 no-usable-input guard, but the plan should say so concretely.** The 0006 guard (pipeline.py:328-334) keys off `raw_data` presence, independent of `run_transform`'s skip — so loosening the skip cannot make a no-input run falsely pass (verified). Add an explicit regression assertion that an **all-empty `raw_data`** run still raises the 0006 RuntimeError (exit 1) *and* that `run_transform` with all-empty sources still skips every entity (the `all(df.empty …)` branch at proposed line 36). Also: the `transformed.empty` net (line 73 test) is sufficient as a *backstop*, but call out one gap to confirm in testing — is there any multi-source entity where empty-primary + populated-secondary yields a **non-empty wrong** output that bypasses the net? Verified answer for the current entities: **no** (Enrollments/Classes return empty on empty schedule; Attendance unions only role-resolved bands). State this conclusion in the plan so the reviewer's check is recorded, and add the "non-attendance multi-source entity with empty primary still produces correct output or empty" assertion (e.g. Enrollments with empty schedule but a populated demographic → still empty, no crash).

6. **[Docs/Stage-9]** All three items currently live in `ROADMAP.md ## Later` (lines 10, 12, 14). The plan must **remove the three fixed entries** from ROADMAP as part of the landing slices (the plan's Goals imply this but the Decomposition/Test sections don't list the ROADMAP edit). Add to each slice's done-list: remove its ROADMAP `## Later` bullet + append a dated DECISIONS one-liner. No ARCHITECTURE_TREE change is needed (no files added/moved/removed) — state that explicitly ("no tree change this plan") so the tree-gate expectation is clear.

### Sizing / completeness check (per slice)
- **Slice 1 (Item 1, attendance skip)** — **OK.** `pipeline.py` + tests, one file of logic, reproduce-first satisfied (synthetic period-only run). Lands complete. Add the #5 assertions.
- **Slice 2 (Item 2, append_year_to_id)** — **OK as scoped, but resolve #4 first** (downgrade to doc-note, or single shared helper). As written it risks landing a near-duplicate resilience loop = new (micro) debt, which fails the "no new tech debt" gate. Either resolution keeps it one-session-sized.
- **Slice 3 (Item 3, stale-output prune)** — **OK in size, but BLOCKED on #1** (allow-list correctness) before it can land. ~10 lines + the cross-config test (#2) and purity test (#3). Order 1→2→3 is fine; slices are independent.
- **Reproduce-first** satisfied for the two `bug`-tagged items (Items 1, 2). Item 3 is a `gap`/design-decision, not a `bug` — its test is a behavior test, not a reproduce-first failing test; that's correct, but note the plan tags it correctly (don't apply `bug` discipline to it).

### Harness impact
- **No new STANDARD or gate.** All within live dimensions: `reliability-resilience`, `data-and-persistence (integrity)`, `maintainability-structure`, `testing`. The plan's in-scope list (lines 66-69) is right.
- **One harness candidate (Stage-9 (a)):** the Item-3 finding — "an allow-list derived from `mappings.keys()` includes inherited-but-disabled entities; use the resolved `enabled_entities` set for any delete/prune decision" — is a recurring foot-gun (config inheritance defines more than it enables). Worth a `claugentic-DECISIONS.md` line and possibly a CLAUDE.md note under *Output Targeting* so the next prune/cleanup author doesn't repeat it. Record at Land if #1 is implemented.
- **No CI on the stacked PR** is correctly called out (line 4, 76); the local full-suite + SD74 gate per slice is the right mitigation. Keep the SD74 byte-identical assertion — verified none of the three paths fire on SD74.

### Resolution _(orchestrator)_

Both reviews folded. Two **material** changes from the draft (re-presented to the user at approval): **Item 2 cut to a doc/decision** and **Item 3 → warn-only (auto-prune deferred)**. The Spec below is authoritative.

1. **Item 1 — proceed as drafted + the reviewer's #5 assertions.** Verified safe (every multi-source transformer returns empty on an empty primary; the `transformed.empty` net backstops; the 0006 no-usable-input guard is independent of the skip). Tests add: 0006 all-empty-raw_data still raises (exit 1); a non-attendance multi-source entity with empty primary + populated secondary (Enrollments: empty schedule + populated demographic) → still empty, no crash.

2. **Item 2 — CUT the code change → a documented won't-fix (plan-reviewer #4a + yagni firm CUT).** `generate_class_id` is `row.get` + an f-string — it cannot raise; a per-row rewrite would defend an impossible failure and its only test would have to *fake* a failure the code can't produce (and add a second near-duplicate resilience loop = micro-debt). Instead: **tighten the docstring note** at `base.py:752` (the branch is intentionally column-level because the helper performs no fallible op; promote to per-row only if it ever gains parse/IO) + a dated **DECISIONS** line. Remove the ROADMAP entry as **resolved-by-decision**. No code change, no test.

3. **Item 3 — change recommendation to Option C (warn-only); auto-prune (A/B) DEFERRED.** The plan-reviewer's #1 is correct that `mappings.keys()` causes cross-config data loss — but the proposed fix (`known = enabled`) does **not** repair the original gap: a *dropped* entity is, by definition, no longer in `enabled_entities`, so an `enabled`-keyed prune never removes it; and it would additionally **delete a last-good CSV** when an enabled entity legitimately has no data this run (partial input). **Conclusion: no auto-prune semantics is cleanly safe under `_base` inheritance** (`mappings.keys()` → cross-config delete; `enabled` → misses config-drop + deletes last-good). So auto-delete is the wrong tool for a PII feed. **Adopt warn-only:** after a successful `save_all`, detect entity CSVs on disk **not emitted this run** and log a **WARNING** (non-destructive — a broad known-set is safe because nothing is deleted). This *addresses the gap* (the stale file is surfaced, not silent) without any data-loss path. Auto-prune stays in ROADMAP with the foot-gun documented.

4. **Slicing → 2 commits** (Item 2 is now a doc): **Commit 1** = Item 1 (`pipeline.py` + tests + its ROADMAP/DECISIONS); **Commit 2** = Item 3 warn-only (`loader.py` + `pipeline.py` + test) **+** Item 2 doc note (`base.py` docstring) + both DECISIONS lines + ROADMAP edits. No ARCHITECTURE_TREE change (no files added/moved/removed).

5. **Stage-9 harness candidate (record at Land):** "an allow-list derived from `mappings.keys()` includes inherited-but-disabled entities (`_base` defines more than a config enables) — derive any delete/prune decision from `enabled_entities`, never `mappings.keys()`." → DECISIONS line + a CLAUDE.md *Output Targeting* note.

_Same-model review (Opus family); honest — independence of role + clean context, not model._

---

## Spec _(Stage 4 — 2 commits)_

### Plain-English (read first)
- **What this builds:** (1) an attendance run that only has the **period** file now produces `StudentAttendance.csv` instead of silently producing nothing; (2) a **documentation** decision that the class-ID field stays column-level-resilient (the helper can't fail, so per-row would defend nothing); (3) after each run, if the output folder still contains an entity CSV this run **didn't** produce (e.g. left over from a prior, now-disabled entity), DistrictSync **logs a warning** naming it — so a stale file can't ship unnoticed. It does **not auto-delete** (deleting from a shared output folder risks erasing a different config's legitimate file).
- **What "done" means for you:** normal runs are byte-identical (SD74 proves it). The attendance bug has a test that fails today and passes after. The stale-file warning has a test proving it flags the stale file and **touches nothing on disk**.
- **What you're accepting:** (1) the entity-skip rule changes from "first source empty" to "all sources empty" (safe — verified). (2) we **don't** add per-row handling to the class-ID field (it can't fail) — recorded as a decision. (3) stale files are **warned, not auto-removed** — you're accepting a log warning over an auto-delete that could erase a different config's output. (Auto-prune stays on the backlog with the reason it's unsafe.)

### In-scope dimensions
`reliability-resilience` · `data-and-persistence` (integrity) · `maintainability-structure` · `testing`. Non-negotiables: fail loudly · single source of truth · **SD74 byte-identical**.

### Commit 1 — Item 1: skip only when ALL sources are empty (`bug`, reproduce-first)
- **`src/etl/pipeline.py` `run_transform`:** replace the `primary_source = source_files[0]; primary_df = raw_data.get(...); if primary_df.empty: skip` block (~140-145) with:
  ```python
  source_frames = [raw_data.get(sf, pd.DataFrame()) for sf in source_files]
  if all(df.empty for df in source_frames):
      logger.warning(f"All source files {source_files} are empty for '{entity_name}'; skipping.")
      continue
  primary_df = source_frames[0]   # may be empty for a role-resolved entity (period-only attendance)
  ```
  The downstream `transformed = transformer.transform(primary_df, ...)` + `if transformed.empty: skip` are unchanged.
- **Tests** (`tests/test_pipeline_required_input.py` or a focused new file):
  - **reproduce-first:** a synthetic `sd51attendance`-shaped `run_transform` with the daily frame empty + the period frame populated → today `StudentAttendance` is absent from `outputs`; after the fix it is present. (Assert it fails on the unchanged code first.)
  - all-empty sources for an entity → still skipped (the new `all(... .empty)` branch).
  - **0006 regression:** `run_pipeline` with all-empty `raw_data` still raises the no-usable-input `RuntimeError` (exit-1 path) — loosening the skip didn't weaken the input guard.
  - **empty-primary net:** a non-attendance multi-source entity (Enrollments) with an empty schedule but populated demographic → `transform` returns empty → entity still skipped, no crash, no file.
- **Docs:** remove the period-only `## Later` bullet from `docs/claugentic-ROADMAP.md`; append a dated `docs/claugentic-DECISIONS.md` line.

### Commit 2 — Item 3 (warn-only stale detection) + Item 2 (doc) 
- **`src/etl/loader.py`:** add
  ```python
  def detect_stale_outputs(self, emitted: set[str]) -> list[str]:
      """Return sorted entity CSV filenames present in the output dir that were
      NOT produced this run (possibly stale from a prior/different run).
      NON-DESTRUCTIVE — detection only; the caller decides what to warn."""
  ```
  It lists `*.csv` files in `output_path` whose stem is a recognized entity name (from the transformer registry / a passed known-set) and not in `emitted`. Deletes nothing. (Known-set breadth is safe precisely because nothing is removed.)
- **`src/etl/pipeline.py` `run_pipeline`:** after a successful `save_all`, call `loader.detect_stale_outputs(set(outputs))`; if non-empty, `logger.warning("Possibly-stale entity CSV(s) in the output dir not produced by this run: [...] — they were not refreshed and may ship stale.")`. (Minimal: log only; no run-log/exit change.)
- **`src/etl/transformers/base.py` (Item 2 doc):** tighten the `apply_field_map` docstring note (~752) — the `append_year_to_id` branch is intentionally **column-level** resilient because `generate_class_id` performs no fallible operation (`row.get` + f-string); promote to per-row only if it ever gains parse/IO.
- **Tests** (`tests/test_loader.py`): pre-seed the output dir with a stale `Classes.csv` **and** an unrelated `notes.txt` **and** a cross-config `CourseInfo.csv`; call `detect_stale_outputs({"Students","Staff"})` → returns `["Classes.csv","CourseInfo.csv"]` (sorted), `notes.txt` **not** listed, and **every pre-seeded file still exists on disk** (nothing deleted).
- **Docs:** in `docs/claugentic-ROADMAP.md` — remove the `append_year_to_id` bullet (resolved-by-decision) and **replace** the stale-output bullet with "stale entity CSVs now **warned** after each run; safe auto-prune deferred (cross-config data-loss under `_base` inheritance)". Append `docs/claugentic-DECISIONS.md` lines for (a) Item 2 won't-fix, (b) Item 3 warn-only + the `enabled_entities`-not-`mappings.keys()` foot-gun. Add a one-line **CLAUDE.md** *Output Targeting* note on the foot-gun.

### Revision (post-approval, 2026-06-24) — Item 3: warn-only → **archive-the-stale**
User chose to **archive** stale outputs rather than warn (safer than the deferred auto-delete: non-destructive, so the cross-config foot-gun can't cause data loss). Verified enabler: the SFTP uploader globs `output_dir.glob("*.csv")` — **top-level only, non-recursive** (`uploader.py:182`) — so an `archive_<ts>/` subfolder is **auto-excluded from delivery**. Item 3 becomes:
- **`src/etl/loader.py`:** keep `detect_stale_outputs(emitted) -> list[str]` (pure, registry-keyed detection). Add `archive_stale_outputs(self, emitted: set[str]) -> list[str]`: detect stale entity CSVs, lazily `mkdir` `output_path/archive_<timestamp>/`, **move** each stale file in via `os.replace` (atomic same-fs), return archived names. Best-effort: a per-file move failure is logged (ERROR) and does not fail the run (the roster already committed + delivered). Deletes nothing.
- **`src/etl/pipeline.py` `run_pipeline`:** after a successful `save_all`, call `loader.archive_stale_outputs(set(outputs))`; if non-empty, `logger.info("Archived N stale entity CSV(s) not produced by this run into archive_<ts>/ (excluded from SFTP): [...]")`. No exit/run-log change.
- **Tests (`tests/test_loader.py`):** pre-seed `Classes.csv` + `CourseInfo.csv` + `notes.txt`; `archive_stale_outputs({"Students","Staff"})` → both stale CSVs **moved** into `archive_<ts>/` (gone from top-level, present in the archive subdir), `notes.txt` untouched (top-level), returns the two names; **assert `output_dir.glob("*.csv")` no longer lists them** (proves SFTP won't send them); a clean run (no stale) creates no archive dir + returns `[]`; an emitted entity is never archived. Keep the `detect_stale_outputs` pure-detection test.
- **Docs:** ROADMAP — the stale-output item is now **resolved** (archive), so **remove** its `## Later` bullet (don't leave a "deferred" note). DECISIONS — Item 3 line becomes "stale entity CSVs **archived** (non-destructive `os.replace` move into `archive_<ts>/`, excluded from SFTP's top-level `*.csv` glob) instead of deleted — sidesteps the cross-config data-loss that blocked an auto-delete." CLAUDE.md *Output Targeting* note — reword to: stale entity CSVs are **archived** into `archive_<ts>/` (non-destructive, SFTP-excluded), NOT deleted; any future *delete* would still need to derive from `enabled_entities` (not `mappings.keys()`, which includes inherited-but-disabled entities). Item 2 (doc) and Item 1 unchanged.

### Acceptance / gates (local — no CI on this stacked PR)
Full `pytest --cov` (80%) · **SD74 snapshot byte-identical** · the new tests · `ruff check` + `ruff format --check` · `mypy src/ --exclude 'src/ui'` (ignore the known local-only `classes.py:130` pandas-stubs discrepancy — green in CI) · `bandit -r src/ -q -c pyproject.toml` · config validation · `scripts/claugentic-check_architecture_tree.py` (no tree change expected).
