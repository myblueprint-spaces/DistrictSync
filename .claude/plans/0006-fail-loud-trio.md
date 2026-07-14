# 0006 — Fail Loud, Don't Fake Success (the trio)

- **Status:** Spec'd (plan-review CHANGES REQUIRED + yagni cuts folded → see Resolution + Spec below; awaiting user approval)
- **Roadmap item:** audit (2026-06-23) — three "false green" defects: silent blanked column · no-usable-input exits 0 · Run History green on SFTP failure
- **References:** stacks on `feat/unify-conversion-pipeline` (commit `5936399`) — `pipeline.py` already has `run_transform`/`compute_anomalies`/`TransformOutputs`; `DataLoader` has `select_ordered`/`csv_encoding`. `docs/claugentic-ARCHITECTURE_TREE.md` · `CLAUDE.md` (exit-code contract, fail-loudly) · `docs/claugentic-DECISIONS.md`.

## Problem

DistrictSync runs **unattended on a schedule** for **non-technical** district staff and moves **student PII** into SpacesEDU. The worst failure mode is a run that **reports success while it actually failed or silently lost data** — nobody is watching the console, so a false green propagates stale/corrupt rosters to the partner with no human catching it. Three places do this today:

1. **`apply_field_map` swallows a per-field exception and blanks the whole output column** (`src/etl/transformers/base.py:753-755`). The `except Exception` handler logs but sets `result[tgt_field] = pd.NA`, so a transform that raises mid-column produces a wholly-blank column and the run still exits 0 / status `success`. Silent data corruption — the PR #12 class.
2. **A missing/empty *required* GDE input yields empty output but the run still exits 0.** `extractor.load_data` maps an absent file to an empty DataFrame (`extractor.py:52-55`); `run_transform` skips any entity whose primary source is empty (`pipeline.py:133-135`). A scheduled run that received **no usable input** (wrong folder, truncated export, locked file) looks identical to a clean run.
3. **Run History shows green "Success" when SFTP delivery failed.** The CLI exits 3 on delivery failure (`main.py:289-297`), but `_emit_run_log` is always called with `status="success"` when ETL completes (`pipeline.py:321`) and `03_Run_History.py:84` derives Status purely from `status == "success"`. Task Scheduler shows red-3 while Run History shows green-✅ — contradictory.

## Goals

- A transform/mapping **bug** (unexpected exception) can never silently blank a column behind a green run.
- A scheduled run that received **no usable required input** fails visibly (non-zero exit, `failed` run-log).
- A run where ETL succeeded but **delivery failed** never displays as plain green "Success" — the exit code and Run History agree.
- Each change ships a regression test proving the *old* silent behavior now fails loud.

## Non-goals

- **No happy-path output change** — a clean run with all required files and no transform errors is byte-identical; **SD74 snapshot stays green & byte-identical.**
- **No gold-plating** — no retry/backoff, no new config schema beyond the minimum, no reworking the exit-code contract beyond what's needed, no touching genuinely-optional-file handling.
- Don't change `compute_anomalies`, diff, quality, or the SFTP uploader internals.
- Don't turn the *legitimately absent source column* path (`base.py:744-745`/`750-751`, intended `pd.NA`) into a failure — only the *unexpected-exception* path changes.

## Approach (grounded in the real functions)

### Finding 1 — `apply_field_map` (`base.py:703-757`)
Three outcomes are conflated today: **intended blank** (config column not present → `pd.NA`, lines 744-745/750-751 — keep), **config error** (unknown transform → already `raise ValueError`, lines 735-739 — keep), and **unexpected exception** (the transform function itself raised → caught at 753, logged, column blanked, run continues green — **the bug**). Fix: **narrow the handler so an unexpected exception re-raises** with entity/field context. The intended-blank branch is reached by an explicit `if column_name in working.columns` check, independent of the `try/except`, so removing the swallow does not change intended-blank behavior. A raised exception propagates `transform` → `run_transform` loop → `run_pipeline`'s outer `except` (`pipeline.py:332-336`), which already emits `_emit_run_log("failed", …)` + re-raises; `main.py:274-277` prints + `sys.exit(1)`. **The fail-loud plumbing already exists — we only stop swallowing.**

### Finding 2 — required-input emptiness (`pipeline.py`)
The guard belongs in `run_pipeline` (knows both the required set *and* the loaded frames), **not** in `extractor.load_data` (shared with `load_from_bytes`; must keep "absent → empty frame, skip" for optional files + the Convert partial-upload UX). After `load_data`, check the **primary** source of each enabled entity; if **every** enabled entity's primary frame is empty (missing or zero rows) → **fail loud** (ERROR naming the missing/empty files, raise → existing outer handler writes `failed` + exits non-zero). A run with *some* usable input proceeds (per-entity skip-on-empty stays legitimate).

### Finding 3 — Run History status honesty (`03_Run_History.py`)
The run-log already carries `sftp_attempted`/`sftp_ok` (`pipeline.py:216-217`); **no new state.** Derive the display: `status=="success"` but `sftp_attempted and not sftp_ok` → distinct amber **"ETL OK · SFTP FAILED"** instead of green ✅. `_emit_run_log`'s `status` stays `success`/`failed` (reflects whether the **ETL** completed — honest; SFTP is a separate axis already in the record).

## Design decisions for the user (the three judgment calls)

### Decision 1 — `apply_field_map`: raise vs warn-loud on an unexpected transform exception
When a column's calculation hits an *unexpected* crash (not the legitimate absent-source-column case, which is unchanged):
- **Option A — Raise (fail the whole run). [RECOMMENDED]** Stops with *"Error transforming Students.Email Address: <cause> — run halted to avoid emitting a corrupted column."* Exits non-zero (1), Run History ❌ Failed, **no** corrupted file delivered. *Why:* a silently-blanked Email/Grade/User-ID column shipped to SpacesEDU is worse than a visible failure; matches "fail loudly" + the PR #12 lesson; smallest honest change (plumbing exists).
- **Option B — Warn loud + record, continue.** Blank only the failing column, log ERROR + add to a run-log `errors:[]`, exit 0. *Why not:* a partial roster with one critical column blanked still ships; "continue but record" suits a *recoverable* anomaly (the >20% warning), not an unexpected exception; more code, worse safety.

### Decision 2 — required-input failure: when is empty a failure, and what exit code?
- **Option A — Fail only when *all* enabled primaries are empty; reuse exit 1. [RECOMMENDED]** "No usable input at all" is unambiguously failed; exit 1 already means ETL/validation error; no new contract surface; respects optional files (only fires when nothing loaded).
- **Option B — Fail when *any* required primary is empty.** *Why not:* too aggressive — districts legitimately may not provide every entity's source daily; fights tested per-entity skip-on-empty.
- **Option C — A new exit code (e.g. 4).** *Why not:* YAGNI — code 1 already covers "run did not complete."

### Decision 3 — Run History status when ETL ok but SFTP failed
- **Option A — Derive amber "ETL OK · SFTP FAILED" from the existing booleans. [RECOMMENDED]** Single source of truth (booleans already in every record, can't drift from the exit-code decision); ~3-line change in one UI file; historical records render correctly.
- **Option B — Add a third `status` value (`"sftp_failed"`).** *Why not:* second source of truth that can disagree; UI handles three states; no help for old records.

**Plan recommends Option A for all three** — the user may pick a different option per decision before the spec is written.

## Affected files
- `src/etl/transformers/base.py` — narrow `apply_field_map`'s `except Exception` (~753) so an unexpected exception re-raises with entity/field context; keep absent-column → `pd.NA`.
- `src/etl/pipeline.py` — `run_pipeline` boundary guard after `load_data` (raise → existing outer handler → exit 1). No `_emit_run_log` signature change.
- `src/ui/pages/03_Run_History.py` — Status derivation (~84): amber "ETL OK · SFTP FAILED" when `status=="success"` and `sftp_attempted and not sftp_ok`. Extract the pure rule into a tiny testable helper (no Streamlit import) so it's unit-testable despite `src/ui/*` coverage-omit.
- `tests/test_transform_base.py` — transform that raises now propagates (was blanked); intended absent-column still `pd.NA`.
- new `tests/test_pipeline_required_input.py` — all-empty/missing required input → raises + `failed` run-log + exit 1; partial input still runs.
- `tests/test_sftp_exit.py` — assert the SFTP-failure run-log carries `status="success"`, `sftp_attempted=True`, `sftp_ok=False`.
- new `tests/test_run_history_status.py` — the pure status-derivation rule (success / ETL-OK-SFTP-FAILED / failed).
- `CLAUDE.md` — one line: "no usable required input" is a code-1 failure; `apply_field_map` fails loud on unexpected transform exceptions.
- `docs/claugentic-DECISIONS.md` — dated entry recording the three decisions.
- `docs/claugentic-ARCHITECTURE_TREE.md` — refresh `base.py`/`pipeline.py`/`03_Run_History.py` lines if their summaries no longer reflect fail-loud behavior (tree-check gate).

## Decomposition into slices
The three are largely independent (different files, no shared new abstraction). **Recommend A then B:**
- [ ] **Slice A — Findings 1 + 3 (transform fail-loud + Run History honesty).** Both tiny, both "stop faking success," neither changes pipeline orchestration. One coherent "honest status" change, one DECISIONS entry. Gates: full suite + SD74 byte-identical + lint/type/security + tree-check.
- [ ] **Slice B — Finding 2 (required-input boundary guard).** Touches `run_pipeline` control flow — the most behavior-change risk (false failures) — so its own slice with focused tests + SD74-green. *(All three could combine under the size limit; splitting B isolates the only real false-positive risk.)*

**YAGNI check — drop any?** No — all three are genuine "false green" defects in a PII-moving unattended tool; each is a few lines + tests. Keep all three.

## In-scope standards dimensions
- **reliability-resilience** — unexpected transform exceptions + no-usable-input fail loud instead of false green; per-entity graceful skip preserved for the legitimate partial case.
- **observability-ops** — `failed` run-log + ERRORs name *what* failed; Run History status stops contradicting the exit code; exit-code contract stays coherent.
- **data-and-persistence (data integrity)** — no corrupted/blanked column or empty roster written/delivered behind a green run; atomic-write + BOM rules untouched.
- **testing** — each fix ships a regression test proving the old silent path now fails loud; SD74 snapshot the byte-identical happy-path guard.

## Risks & mitigations
- **Slice A breaks a config relying on swallowed exceptions** → SD74 byte-identical proves the real happy-path never hits the exception path; intended absent-column → `pd.NA` preserved + unit-tested.
- **Slice B false failures for a district running few entities** → guard fires only when **all** enabled primaries are empty; existing `test_run_transform` partial behavior unchanged + a "partial input still runs" test.
- **Run History helper in a coverage-omitted UI module** → pure derivation rule in a no-Streamlit helper, unit-tested; page is a thin caller.
- **Outer `except` makes the message generic** → wrap raises with actionable entity/field-specific messages so `error=str(e)` is meaningful.
- **tree-check Stop backstop** → refresh the affected tree lines in the same slice.

## Test strategy
1. **Finding 1:** trigger a transform that raises inside `apply_field_map`; assert it **propagates** (was: blank column). Companion: absent config column still `pd.NA`, no raise.
2. **Finding 2:** `run_pipeline` against an all-missing/empty-required input dir → raises + `_emit_run_log("failed")` + exit 1; partial input → still completes + writes that entity.
3. **Finding 3:** extend `test_sftp_exit.py` (run-log booleans on SFTP failure); new `test_run_history_status.py` for the pure rule (success / ETL-OK·SFTP-FAILED / failed).
4. **SD74 snapshot byte-identical** — happy path fires none of the new branches.
5. **Gates:** full pytest (80%), ruff check + format, mypy (non-UI), bandit, `make validate-config`, tree-check.

## Approval triad (plain English)
- **What this builds:** three small "stop faking success" fixes — (1) an unexpected crash while computing a column **stops the run and says why** instead of quietly delivering a blank column; (2) a scheduled run that gets **no usable input** **fails visibly** instead of reporting clean; (3) when conversion works but the **upload fails**, Run History shows **"ETL OK · SFTP FAILED"** instead of a misleading green ✅ (matching what Task Scheduler already reports).
- **What "done" means for you:** a normal run with good data produces **exactly the same files as today** (proven byte-for-byte by SD74). Only the three failure cases change — each now loud, each covered by a test proving it no longer fails silently. All gates green.
- **What you're accepting:** a run that previously *looked* successful in three broken situations now **fails or warns loudly** (non-zero exit and/or non-green status). For an unattended PII tool, a visible failure an operator can fix beats a silent one that ships bad data. You're choosing **Option A** in each decision above — or pick differently per decision before the spec.

---

## Review  _(filled by plan-reviewer + yagni-sentinel — Stage 3)_

**RUNNING AS: Opus 4.x — SAME-MODEL run** (builder family is Opus per the harness default for judgment work; treat this review as a same-model pass — a reduction of rubber-stamping risk via clean-context independence of role, NOT a model-independent oracle. Model blind spots are not de-correlated.)

**Verdict: CHANGES REQUIRED** — the three fixes are the right direction and the propagation/placement/no-new-state claims are largely true, but **Decision 2 as written has a real false-positive that fails a currently-supported run, Finding 1's grounding misstates current behavior, and Finding 3 ignores an existing UI affordance.** All are plan-level corrections, not redesigns; size is otherwise fine.

### Required changes (numbered, actionable)

1. **Decision 2 — the "all enabled primaries empty" rule fails the legitimate period-only attendance run.** For the `sd51attendance` tier the *only* enabled entity is `StudentAttendance`, whose **positional primary is `StudentDailyAbsences.txt`** (daily band, listed first) — but the `StudentAttendance` transformer resolves bands **by role, order-independent**, and a **period-only** district (no daily file, period file present) is an explicitly *supported* scenario per `DECISIONS.md` 2026-06-19 ("daily empty → period only"). Verified at runtime: with daily empty + period present, `run_transform` already **skips** the entity (positional-primary guard, `pipeline.py:133-135`) and produces zero outputs. Under the proposed guard, "all enabled primaries empty" would be TRUE → **the run hard-fails exit 1** — converting a supported period-only run into a crash (a *false* failure, the exact risk the plan's own Risk section claims is mitigated). **Fix the plan:** either (a) scope the required-input guard to entities whose primary is the *real* gate (exclude `StudentAttendance`, or any entity that resolves its sources by role), or (b) base the guard on "no enabled entity produced **any** output" measured **after** `run_transform` (i.e. `outputs == {}`), not on positional-primary emptiness — option (b) also auto-covers the latent period-only skip without special-casing, and is robust to `enabled_entities` tiers (`mbponly`, `mbp_core`, `sd51attendance`) where "primary" is ambiguous. State which, and add a period-only `sd51attendance` test to the Slice-B test list proving it does NOT raise.

2. **Finding 1 / Decision 1 — the grounding misstates today's behavior of the unknown-transform `raise ValueError`.** The plan says the unknown-transform raise (`base.py:735-739`) is "already `raise ValueError` … keep" as though it already fails loud. It does **not**: that `raise` is **inside the same `try`** and is **currently swallowed to `pd.NA`** by the `except Exception` at line 753 (verified at runtime — returns `[<NA>, <NA>]`, only logs a traceback). So narrowing/removing the swallow changes **two** paths to fail-loud (unknown-transform AND unexpected-exception), not one. This strengthens the fix but the spec must (a) state the corrected current behavior, and (b) add a regression test that an **unknown transform now propagates** (today it silently blanks) — not just the "transform function raised" case. The intended-absent-column → `pd.NA` branches (744-745 / 750-751) ARE independent of the handler (reached by `if column_name in working.columns`), so the keep-intended-blank claim is correct.

3. **Finding 3 — the plan ignores the existing `SFTP` column and slightly over-claims the win.** `03_Run_History.py:91` **already** renders a dedicated `SFTP` column showing `❌` when `sftp_attempted and not sftp_ok`. So the operator is *not* fully blind today — the contradiction is narrower than "shows green Success": the **Status** cell says ✅ while the **SFTP** cell already says ❌ in the same row. The fix (amber "ETL OK · SFTP FAILED" Status) is still correct and worth doing (the Status column is the at-a-glance signal), but the plan must (a) acknowledge the existing SFTP column so the change is additive/coherent (don't duplicate or remove it), and (b) tone the Problem statement from "shows green Success" to "the **Status** cell shows green while the SFTP cell already flags failure — the at-a-glance Status contradicts the exit code." Confirm the new helper and the existing SFTP-column logic share the same boolean source (no drift).

4. **Honesty of the "fail loud" label (Decision 1 vs Decision 3).** Findings 1 and 2 genuinely **fail** (non-zero exit). Finding 3 only **re-labels a display** — it does not change exit codes or run-log `status` (correctly, per the plan). Ensure the Goals/Approval-triad wording does not let Finding 3 read as "the run now fails" — it's purely presentational (Run History already had the SFTP ❌; exit 3 already fires). The plan mostly gets this right (line 38), but the Goals bullet "never displays as plain green" should explicitly note Status-cell-only, display-only, no state/exit change.

5. **Exit-code coherence (Decision 2) — reusing exit 1 is correct; one doc nuance.** Exit 1 ("ETL/validation error — run did not complete") fits "no usable input". But note the asymmetry the spec should document: a *partial* run that silently dropped some entities (some primaries empty, not all) still exits **0** — that is intended (per-entity skip-on-empty is legitimate), but the CLAUDE.md one-liner should say "**no usable required input at all** is exit 1; a partial run with some empty sources stays exit 0 by design" so the contract reads coherently and nobody later "fixes" the partial case into a failure.

### Sizing / completeness check (per slice)

- **Slice A (Findings 1 + 3) — OK, lands complete.** Both are small, file-local (`base.py` handler narrowing + `03_Run_History.py` helper), no pipeline-orchestration change, SD74 unaffected (happy path never hits the handler; verified the SD74 regression runs the full pipeline via `main()` with all primaries populated). Tests + DECISIONS + tree-line refresh are listed. Apply required changes 2, 3, 4 before spec. One coherent "honest status" slice — fine.
- **Slice B (Finding 2) — OK shape, but BLOCKED on required change 1.** Correct instinct to isolate the only false-positive-risk change in its own slice. It does **not** interact badly with the just-landed `run_transform` (the guard sits in `run_pipeline` after `run_transform` returns; if implemented as "outputs == {}" per option (b) it actually composes cleanly with the existing positional-primary skip). But the guard's *definition* must be fixed (change 1) or it will fail the `sd51attendance` period-only run. Add the period-only non-raise test. With change 1 applied, the slice lands complete with SD74 green.
- **Decomposition A-then-B — correct.** All three could combine under the size limit; splitting B to isolate the false-positive risk is the right call. No slice exceeds a single session.

### Harness impact

- **No new STANDARD or agent needed.** This reinforces existing live dimensions (`reliability-resilience`, `observability-ops`, `data-and-persistence`, `testing`) — no new lens.
- **DECISIONS.md** entry required (plan lists it) — must record (a) the corrected fact that unknown-transform was *also* swallowed (not just unexpected exceptions), and (b) the chosen Decision-2 guard definition + why it excludes/handles the role-resolved `StudentAttendance` band case.
- **CLAUDE.md** one-liner: keep it to the exit-1 "no usable required input" rule + the apply_field_map fail-loud note; add the partial-run-stays-0 nuance (change 5). Index, don't duplicate.
- **ARCHITECTURE_TREE.md** line refresh for `base.py` / `pipeline.py` / `03_Run_History.py` if summaries no longer reflect fail-loud — listed; keep.
- **Consider a ROADMAP gate item (Stage-9 (b)):** the *positional-primary vs role-resolved-source* mismatch for `StudentAttendance` (a period-only run silently produces nothing **today**, independent of this plan) is a latent correctness gap a checklist could catch. Log it to ROADMAP rather than expanding this plan's scope (YAGNI — don't fix it here, but don't let the new guard mask it).


### Resolution _(orchestrator)_

All 3 plan-reviewer required changes + both yagni-sentinel cuts folded. The corrections below **supersede** the draft Approach/Decision text where they differ; the Spec is authoritative.

1. **Decision 2 guard → fail when NO USABLE INPUT was loaded** — checked right after `load_data` as `not raw_data or all(df.empty for df in raw_data.values())` (every required file missing/empty), **not** `outputs == {}`. Rationale (corrects the reviewer's option-b): `outputs == {}` would still false-fail a **period-only `sd51attendance`** run, which produces zero outputs *today* because `run_transform`'s positional-primary skip drops the entity when the daily file is empty (a separate latent bug). Keying off **input presence** cleanly separates 'no input at all' (fail loud, exit 1) from 'input present but an entity got skipped' (unchanged — exit 0; the latent skip bug is neither masked nor fixed here). Partial multi-entity runs stay exit 0.
2. **Finding 1 — REVISED to row-resilient + loud (user-directed; supersedes the draft's 'raise').** A transform error must NOT fail the whole run or blank the whole column. Today `series.apply(func)` (~741) aborts the ENTIRE column if any one row raises (every student loses that field) and the run reports success. New behavior: apply the transform **per-row-resiliently** — a row whose `func` raises gets `pd.NA` in **that cell only**; valid rows keep their existing correct value (the accurate transform path is untouched, SD74 byte-identical). Failures are **aggregated per (entity, field)** into `context.data_errors`, logged at ERROR, recorded in the run-log (`data_errors` summary), and surfaced in Run History as **'Completed with N data errors'** — never silent. The run still completes + delivers (exit 0). The unknown-transform `ValueError` (~736, also swallowed today) and any column-level error are recorded the same loud way + the column blanked + continue (do NOT raise) — config-load validation of `ALLOWED_TRANSFORMS` is the cleaner fail-fast fix, backlog T2.4, out of scope. Intended absent-column → `pd.NA` is NOT an error and is NOT recorded.
3. **Finding 3 is display-only + additive.** The `SFTP` column already shows ❌ (`03_Run_History.py:91`); only the at-a-glance **Status cell** changes (amber "ETL OK · SFTP FAILED"), sharing the same boolean source — no exit/run-log `status` change. yagni: **inline ternary, no helper extraction, no new test file** — the booleans assertion folds into `test_sftp_exit.py`.
4. **One slice** (yagni): the three fixes land together (one branch, one DECISIONS entry). The false-positive risk that motivated an A/B split is gone now the input guard is presence-based and Finding 1 is non-fatal.
5. **Exit-code doc nuance:** CLAUDE.md notes "no usable required input at all → exit 1; a partial run with some empty sources stays exit 0 by design."

_Noted (not written to ROADMAP, per the lean-docs directive): the positional-primary-vs-role-resolved mismatch means a period-only attendance run produces nothing **today**, independent of this plan; the `outputs=={}` guard neither masks nor fixes it — out of scope here._ **Same-model review (Opus); honest.**

---

## Spec _(Stage 4 — one slice)_

### Plain-English (read first)
- **What this builds:** three "stop faking success" fixes — (1) when a row's value makes a column transform crash, **only that one cell goes blank** (today the *whole column* blanks for every student) — the error is **recorded and shown** in Run History ("Completed with N data errors"), and the rest of the file is still produced and delivered; (2) a run that received **no usable input at all** **fails (exit 1)** instead of reporting clean; (3) when conversion works but the **upload fails**, Run History's **Status cell** shows amber **"ETL OK · SFTP FAILED"** (it already had a separate red SFTP ❌ column; now the at-a-glance Status agrees with the exit code).
- **What "done" means for you:** a normal run with good data produces **byte-for-byte the same files as today** (SD74 proves it). Only those three failure cases change — each loud, each tested.
- **What you're accepting:** (1) a row with a genuinely bad value ships with **that one field blank** (flagged in Run History) — not the whole column silently blanked, and not the whole roster blocked; the rest delivers normally. (2) the run **fails (exit 1) only when it got no usable input at all** (a partial run still succeeds + delivers). (3) a display-only amber Status when delivery failed. This surfaces every error loudly while never needlessly withholding good data.

### In-scope dimensions
`reliability-resilience` · `observability-ops` · `data-and-persistence` (integrity) · `testing`. Non-negotiables: fail loudly · validate at boundaries · single source of truth · **SD74 byte-identical**.

### Files & changes (one slice)
- **`src/etl/transformers/base.py` — `apply_field_map` (~703-757):** make the transform application **row-resilient**. Replace `series.apply(func)` (~741) with a per-row safe application — a row whose `func` raises gets `pd.NA` for **that cell only**; valid rows keep their existing correct value. **Record** each failure (entity, field, cause, failed-row count + a sample) into `context.data_errors`. The **unknown-transform `ValueError`** (~736) and any column-level structural error: log ERROR + record into `context.data_errors` + blank that column + continue (do NOT raise; do NOT silently swallow). Preserve the intended-blank branches exactly (`if column_name not in working.columns -> pd.NA`, ~744/750) — those are NOT errors and are NOT recorded.
- **`src/etl/transformers/context.py` — `TransformContext`:** add a `data_errors: list[dict]` field (default empty) the transformers append to; carried for the whole run.
- **`src/etl/pipeline.py` — `run_pipeline`:** immediately after `raw_data = extractor.load_data(...)`, if `not raw_data or all(df.empty for df in raw_data.values())` (no usable required input was loaded) → log an actionable ERROR naming the missing/empty required files and **raise** (the existing outer `except` writes `_emit_run_log("failed", …)` + re-raises → `main.py` exit 1). Otherwise proceed unchanged. The guard keys off **input** (`raw_data`), independent of `run_transform`'s per-entity skipping — so a period-only attendance run (period file present) does NOT fire it. No change to `extractor`/`load_data`.
- **`src/etl/pipeline.py` — `run_pipeline` (data-errors surfacing):** after `run_transform`, read the run's `context.data_errors`; if non-empty, log a consolidated ERROR and pass a compact summary (total count + per-field counts) into `_emit_run_log` as a new `data_errors` field. ETL `status` stays `success` (the run completed + delivered) — `data_errors` is a separate axis, like `sftp_*`.
- **`src/ui/pages/03_Run_History.py` — Status derivation (~84):** inline branch — when a record is `status=="success"` and `sftp_attempted and not sftp_ok`, render the Status cell amber **"⚠️ ETL OK · SFTP FAILED"** instead of green "✅ Success". Additive to the existing `SFTP ❌` column (~91), same booleans. **Also:** when the record carries a non-zero `data_errors`, render the Status cell amber **"Completed with N data errors"** (same display-only pattern; show alongside the SFTP-failed flag if both). No helper, no new file, no run-log/exit change.
- **`CLAUDE.md`:** `apply_field_map` is **row-resilient** — a row whose transform raises blanks only that cell and is recorded in the run-log `data_errors` (Run History shows "Completed with N data errors"); a column-level/unknown-transform error blanks that column + records (never silent), and the run still delivers. A run with **no usable required input** exits 1; a **partial** run stays exit 0 by design.
- **`docs/claugentic-DECISIONS.md`:** dated entry — the three fixes + the corrected facts (unknown-transform was also swallowed; the `outputs=={}` guard choice).
- **`docs/claugentic-ARCHITECTURE_TREE.md`:** refresh `base.py` / `pipeline.py` / `03_Run_History.py` lines if their summaries no longer reflect fail-loud.

### Tests
- `tests/test_transform_base.py`: (a) a transform that raises on **one** row → only that cell is `pd.NA`, the **other rows keep their correct value**, and a failure is recorded in `context.data_errors` (was: whole column blanked, nothing recorded); (b) **unknown transform name** → that column blanked + recorded in `context.data_errors`, no raise (was: silently blanked); (c) config column absent from frame → still `pd.NA`, **not** recorded as an error; (d) all-valid input → `context.data_errors` empty, output unchanged (byte-identical path).
- `tests/test_main_helpers.py`: `_emit_run_log` carries the `data_errors` summary when present, and `status` stays `success` (delivery proceeds). The Run-History inline display branch is covered by the existing UI smoke test.
- new `tests/test_pipeline_required_input.py`: (a) all required files missing/empty → `run_pipeline` raises + `_emit_run_log` `status="failed"`; `main` wiring → exit 1; (b) **period-only `sd51attendance`** (daily absent/empty, period present) → the input guard does **NOT** fire (the period file is non-empty in `raw_data`) — asserts the run does not raise from the new guard; (c) partial multi-entity input (one entity has data) → completes + writes it, exit 0.
- `tests/test_sftp_exit.py`: assert the SFTP-failure run-log carries `status="success"`, `sftp_attempted=True`, `sftp_ok=False` (the Status-cell source).

### Acceptance / gates (all green)
Full `pytest --cov` (80%) · **SD74 snapshot byte-identical** · the three test files above · `ruff check` + `ruff format --check` · `mypy src/ --exclude 'src/ui'` · `bandit -r src/ -q` · config validation · `scripts/claugentic-check_architecture_tree.py`.
