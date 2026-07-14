# 0007 — Atomic loader commit (true all-or-none with rollback)

- **Status:** Spec'd (plan-review CHANGES REQUIRED + yagni trims folded → Resolution + Spec below) — **awaiting user approval (Stage 5)**
- **Roadmap item:** audit-fix worklist item 4 (the audit backlog fence was stripped; this prompt is the source). Tag: **`bug`** — a documented invariant (`loader.py:50` "all succeed or none are committed") is violated → reproduce-first (failing regression test, then fix).
- **References:** stacks on `feat/unify-conversion-pipeline` (commits `5936399`, `c2e34a4`) — item 1 + item 3 landed. `src/etl/loader.py` · `tests/test_loader.py` (`TestAtomicWriteRollback`) · `CLAUDE.md` (loader/atomic-write notes) · `docs/claugentic-DECISIONS.md` · `docs/claugentic-ARCHITECTURE_TREE.md:20`.

## Problem

`DataLoader.save_all` (`src/etl/loader.py:45-82`) promises **"all succeed or none are committed"** (docstring `:50`) but the **commit phase is not atomic across files**:

```python
for tmp_file in tmp_dir.iterdir():          # commit loop
    dest = self.output_path / tmp_file.name
    shutil.move(str(tmp_file), str(dest))   # one file at a time, no rollback
```

If the move of file *K* fails after files *1..K-1* have already moved, the output directory is left as a **torn mix**: new versions of *1..K-1*, stale (or absent) versions of *K..N*. The `except`/`finally` then deletes the staging dir — discarding the staged *K..N* — so the torn state is **permanent**. The invariant is violated exactly when it matters most (a partial commit), and this is an **unattended PII tool**: a half-updated roster ships to SpacesEDU with nobody watching.

**Real trigger (not hypothetical):** the common case is a **re-run overwriting yesterday's output**. A district user with `Students.csv` open in **Excel** (or an antivirus/indexer holding a handle) causes `os.replace`/rename of *that* file to fail on Windows with `PermissionError`, while the other entities already committed → torn output.

**Latent secondary defect (same root):** `shutil.move` is **not even atomic per-file** when overwriting an existing target on Windows. `shutil.move` calls `os.rename`, which on Windows **raises** `FileExistsError` if the destination exists, so `shutil.move` falls back to **copy2 + unlink** — a non-atomic copy that can leave a **half-written** `dest` if it fails mid-copy. So today's overwrite path is doubly non-atomic (across files *and* within a file). The common production path (re-run overwrites) hits this.

## Goals

- `save_all` is **truly all-or-none**: on any commit-phase failure the output directory is left **exactly as it was before the call** — every prior file at its prior content, every would-be-new file absent.
- Per-file commit uses an **atomic** primitive (no half-written destination on overwrite).
- A **regression test proves output-dir consistency after a mid-commit failure** (failure on a *later* file, with *earlier* files already moved) — the gap the prompt calls out: the existing `test_partial_failure_leaves_no_tmp_dir` only asserts the **staging dir** is cleaned, never that the **output dir** is consistent.

## Non-goals (YAGNI)

- **No happy-path output change** — a clean run writes byte-identical files; **SD74 snapshot stays green & byte-identical.**
- No new public API, no config, no signature change to `save_all` / `save_to_csv`.
- No cross-filesystem support beyond what exists — staging/backup live **under `output_path`** (guaranteed same filesystem), which is exactly why an atomic `os.replace` is valid here.
- No cleanup of *stale unmanaged* output files (a prior run's entity dropped from `enabled_entities` leaves its CSV behind) — pre-existing, orthogonal; → ROADMAP note, not this slice.
- No fsync/durability-against-power-loss hardening — out of scope for a CSV batch tool; "atomic" here means **the directory is never left torn**, not crash-durable.

## Approach (grounded in the real function)

Replace the bare one-at-a-time commit loop with a **backup-and-restore atomic commit**:

1. **Stage** every entity into `.tmp_<ts>/` — unchanged.
2. **Commit** (new `_commit_staged(tmp_dir, backup_dir)`): iterate staged files in **sorted (deterministic) order**; for each:
   - if `dest` exists, **move the existing target aside** into `.bak_<ts>/` via `os.replace` (atomic, same fs);
   - **record** `(dest, backup|None)` in an `applied` list *before* promoting (so the in-flight file is covered);
   - **promote** the staged file into `dest` via `os.replace` (atomic overwrite).
3. **On any exception during commit** → **roll back** in reverse `applied` order: delete the new file we placed (`dest.unlink()` if present), then restore the backed-up original (`os.replace(backup, dest)`). Best-effort & resilient — each file's restore is wrapped so one failure logs an ERROR and does not abort the rest or mask the original exception; then **re-raise the original**.
4. **`finally`** removes both `.tmp_<ts>/` and `.bak_<ts>/` (`shutil.rmtree(..., ignore_errors=True)`).

**Why `os.replace` (not `shutil.move`):** `os.replace` is the cross-platform **atomic overwrite** primitive (POSIX *and* Windows), eliminating the latent half-written-dest defect. It requires same-filesystem — satisfied because `tmp_dir`, `backup_dir`, and `dest` all live under `output_path`. `shutil` is still imported (used by `rmtree`); add `import os`.

**Why backup-aside (not a directory swap):** `output_path` may hold unmanaged files (run logs, a prior run's extra CSVs); a whole-dir rename would clobber them and change semantics. Per-file backup touches **only** the entities being written — minimal blast radius, and `os.rename`-of-a-directory onto a non-empty target is unreliable on Windows anyway.

**Rollback correctness (per-file states):**
- promote of a *later* file fails → its `dest` was already moved to backup (dest absent) → restore backup→dest; earlier files: unlink new dest, restore their backups → **all originals restored**.
- backup-aside of a file fails → that file is **not** yet in `applied`; its `dest` is the untouched original (rename failed atomically) → earlier files rolled back, this one already correct.
- a *new* entity (no prior file, `backup is None`) that promoted → rollback unlinks it → absent, as before the call. ✔

## Affected files (one slice)
- `src/etl/loader.py` — rewrite `save_all`'s commit; add `import os`; add private `_commit_staged` (backup → promote → rollback). Update the class/`save_all` docstrings to state the backup-and-restore guarantee accurately.
- `tests/test_loader.py` — update `TestAtomicWriteRollback` to drive failure through the new primitive (`os.replace`) and **add the output-consistency regression tests** (below).
- `CLAUDE.md` — one dense line: `save_all` commit is backup-and-restore atomic (`os.replace` per file + `.bak_<ts>/` rollback) → output dir never left torn on a mid-commit failure.
- `docs/claugentic-DECISIONS.md` — dated entry (the fix + the two corrected facts: cross-file torn-commit AND the Windows copy+delete per-file non-atomicity).
- `docs/claugentic-ARCHITECTURE_TREE.md:20` — refine the `loader.py` line so "atomically commits" names the backup/rollback mechanism (the line currently *claims* atomicity the code didn't deliver).
- `docs/claugentic-ROADMAP.md` `## Later` — concise one-liner for the **stale-unmanaged-output-file** gap discovered here (out of scope; don't mask it).

## Design decision for the user (one judgment call)

### Decision — atomic primitive: `os.replace` + per-file backup/restore  **[RECOMMENDED]**
- **Option A — `os.replace` + `.bak_<ts>/` backup-aside, ordered rollback.** Atomic per-file (fixes the latent Windows non-atomicity too); minimal blast radius (only the written entities); same-fs guaranteed by construction; rollback restores exact prior bytes. *Cost:* a brief extra hidden `.bak_<ts>/` dir during commit; one small private method.
- **Option B — keep `shutil.move`, add backup/restore only.** Smaller diff, but leaves the per-file copy+delete non-atomicity unfixed (half-written dest still possible on Windows overwrite) — solves the cross-file tear but not the within-file one. *Why not:* the item is "make it truly atomic"; B is a half-fix that re-bakes the weaker primitive.
- **Option C — write into a fresh dir and swap directories.** *Why not:* clobbers unmanaged files in `output_path`; dir-rename-onto-non-empty is unreliable on Windows; bigger semantic change than warranted.

**Plan recommends Option A.**

## Decomposition into slices
**One slice.** File-local (`loader.py` + `test_loader.py` + doc lines), no orchestration/contract change, lands complete with tests + docs. Well under one session.

## In-scope standards dimensions
- **data-and-persistence (data integrity)** — the all-or-none output-dir invariant is actually upheld now (across files *and* within a file); atomic-write semantics made real.
- **reliability-resilience** — a mid-commit failure (locked file / disk full) leaves a recoverable, consistent state instead of a permanent torn one; rollback is itself failure-resilient (best-effort, never masks the cause).
- **testing** — a regression test captures the *old torn-output* behavior as a failure and proves the fix; SD74 snapshot guards the byte-identical happy path.
- **maintainability-structure** (light) — commit/rollback isolated in one named helper; KISS, no speculative abstraction.

## Risks & mitigations
- **Rollback itself fails** (e.g. disk genuinely full, lock persists) → can't be fully eliminated on a non-transactional filesystem; mitigated by **best-effort, resilient** restore (per-file try/except + loud ERROR) and by re-raising the **original** exception so the operator sees the real cause. Honest scope: "never leaves a torn dir under a *recoverable* failure," not "crash-durable."
- **`os.replace` same-fs requirement** → guaranteed: staging + backup are subdirs of `output_path`; documented in the docstring so nobody later relocates them.
- **Behavior drift on happy path** → SD74 byte-identical snapshot + happy-path `save_all` tests (`test_successful_save_all_*`) prove no change.
- **Existing rollback tests patch `shutil.move`** → they must be repointed to `os.replace`; covered in the test changes (and the diff makes the primitive switch explicit).
- **tree-check** → loader.py is an existing indexed file (no new file); refine line 20 in the same slice so the gate + the description stay honest.

## Test strategy
Replace/extend `TestAtomicWriteRollback`. Failure is injected by patching `src.etl.loader.os.replace` with a side-effect that fails a chosen **promote** (staged→output) while letting **backup** and **restore** moves run for real — so the test exercises the **real rollback code path** end-to-end (true integration, not a mock of rollback):

1. **(new) Mid-commit failure preserves ALL prior output (the called-out gap).** Pre-populate `Students.csv`, `Staff.csv`, `Family.csv` with distinct "original-*" content. Fail the **last** promote. Assert: `save_all` raised; **all three** files still hold their **original** content (none left as the new version); **no `.tmp_*` and no `.bak_*`** dir remains.
2. **(new) Mid-commit failure removes would-be-new files.** Pre-populate **only** `Students.csv` (original); `Staff`/`Family` are new. Fail the last promote. Assert: `Students.csv` == original; `Staff.csv` and `Family.csv` **do not exist** (rolled back to "absent"); no temp/backup dirs remain.
3. **(retained, repointed) Fail-on-first leaves existing output untouched** — `test_rollback_preserves_existing_output`, repointed to `os.replace`.
4. **(retained) Staging+backup cleanup on failure** — no `.tmp_*`/`.bak_*` after any failure (extends `test_*_leaves_no_tmp_dir` to also assert `.bak_*`).
5. **(retained) Happy path** — `test_successful_save_all_writes_all_files` / `*_leaves_no_tmp_dir`, plus assert no `.bak_*` remains and contents are the **new** values.
6. **SD74 snapshot byte-identical** + full gates.

## Approval triad (plain English)
- **What this builds:** makes "save all the output files together" genuinely all-or-nothing. Today, if writing the output fails partway (e.g. one CSV is open in Excel, or the disk fills), you can be left with **some files updated and some not** — a silently mismatched roster. After this, **any** mid-write failure leaves your existing output **exactly as it was**, untouched, and the run fails loudly so an operator can fix the cause and re-run.
- **What "done" means for you:** a normal run produces **byte-for-byte the same files as today** (SD74 proves it). Only the *failure* case changes — and a new test reproduces the old "torn output" to prove it can't happen anymore. All gates green.
- **What you're accepting:** during the commit there's briefly a hidden `.bak_<timestamp>/` folder (auto-removed); on a failure the run aborts with the original error (no partial delivery). On a truly catastrophic failure (the *restore* also fails — disk dead), it's still best-effort — we surface the real error rather than pretend success. You're choosing **Option A** (the atomic `os.replace` + backup/restore) — or pick B/C above before the spec.

---

## Review  _(filled by plan-reviewer + yagni-sentinel — Stage 3)_

> RUNNING AS: Opus 4.x — **same-model-family** as the likely builder (DistrictSync's `implementer-architect` also runs the most-capable/`opus` tier). Independence here is of **role + clean context**, not model: this is a reduction of rubber-stamping risk, not a model-independent oracle. Treat shared blind spots accordingly.

**Verdict: CHANGES REQUIRED** — the approach is sound and correctly diagnosed (quotes verified against `src/etl/loader.py:45-82`, `tests/test_loader.py:96-181`, callers `src/etl/pipeline.py:356` + `src/ui/pages/02_Convert.py:351`; the "no signature/caller change" claim holds, and the only `shutil.move`-patching tests are the three in `TestAtomicWriteRollback` — `test_cli.py:162` and `test_pipeline_parity.py:343` exercise `save_all` only by public behavior, so they are agnostic to the primitive switch). The slice is correctly sized, on the right (lightweight-within-a-stack `bug`) path, and the doc set is right. Changes below close real gaps in the state machine, the test design, and one missed cleanup-ordering hazard — none expand scope.

**Required changes (numbered, actionable):**

1. **Pin the commit *iteration source* and make rollback survive a crash on the FIRST file.** The current loop iterates `tmp_dir.iterdir()` (`loader.py:71`); the plan says "sorted (deterministic) order" but never says the spec must **replace `iterdir()` with a sorted list materialized up front**. Materialize `sorted(tmp_dir.iterdir())` *before* the commit loop and iterate that — so the loop body's own filesystem mutations (promoting into `output_path`, which is *not* `tmp_dir`, so this is mostly safe today, but the backup-dir creation is not) can never perturb iteration, and the order is reproducible in tests. State this explicitly in the spec; it is load-bearing for required-change 3's determinism.

2. **Close the rollback-vs-`finally` ordering hazard the plan glosses.** The plan's step 4 says `finally` removes **both** `.tmp_<ts>/` and `.bak_<ts>/` with `shutil.rmtree(..., ignore_errors=True)`. But rollback (step 3) restores originals **via** `os.replace(backup, dest)` where `backup` lives **inside** `.bak_<ts>/`. The spec MUST guarantee rollback runs to completion **before** the `finally` rmtrees `.bak_<ts>/` — i.e. rollback is inside the `except` block and the `except` re-raises (so control passes through `finally` only *after* restore finishes). This is implied but not stated, and getting it wrong (e.g. cleaning `.bak_` in the same pass as the failure handler, or a future refactor moving rmtree earlier) silently destroys the backups before they are restored → permanent data loss, the exact failure this slice exists to prevent. Make it an explicit invariant in the docstring AND a spec acceptance criterion. Also confirm the happy-path commit (no exception) still rmtrees `.bak_<ts>/` — those backups are now stale-but-valid prior versions and must be removed (the plan says so at line 47; keep it).

3. **The test's promote-vs-backup-vs-restore discrimination is fragile coupling — tighten it.** The strategy (`test strategy` ¶) distinguishes a *promote* `os.replace` (to fail) from *backup/restore* `os.replace` (to delegate to the real one) by **source-path substring** (`.tmp_` vs `.bak_`). This is brittle: it silently couples the test to the internal dir-naming scheme, and a *restore* move's **source** is also under `.bak_` while a *backup-aside* move's **destination** is under `.bak_` — the discriminator must key on the right end of the right call, and "fail the **last** promote" must be pinned to a **specific entity by deterministic sorted order** (required-change 1), not "the 3rd `os.replace` call," because with backup-aside the call sequence is now interleaved (backup, promote, backup, promote, …) and counting calls (as the existing `move_fail_on_third` at `test_loader.py:151` does) will fail the *wrong* operation. Spec the side-effect to: (a) call the real `os.replace` for everything, (b) raise **only** when the *staged source* basename matches the chosen target entity AND the destination is the real `output_path` (a promote), identified by path role, not call count. Add an assertion that the injected failure actually fired on the intended file (guard against a test that passes because nothing failed).

4. **Add the missing test case: backup-aside (not promote) fails.** The two new tests (preserve-all-prior, remove-new-files) both fail a **promote**. But the plan's own rollback-correctness bullet 2 ("backup-aside of a file fails") is a *distinct* code path — the failing file is NOT in `applied`, and earlier files' backups must still restore while the failing file's `dest` is the untouched original. That path is unexercised by the proposed suite, so a bug in the "not-yet-in-applied" boundary (e.g. recording before vs after the backup succeeds) would ship green. Add one regression test that injects failure on a **backup-aside** `os.replace` (an overwrite of an existing target, mid-sequence) and asserts every prior file restored + the failing file untouched + no `.tmp_*`/`.bak_*` left. This is the case the plan's state machine leans on most and tests least.

5. **State the empty-`output_path` / new-entity backup-skip precisely as an acceptance criterion.** The "new entity (no prior file, `backup is None`)" path is described in prose (line 56) but the spec must make it a checked criterion: when `dest` does not pre-exist, **no backup is taken**, `applied` records `(dest, None)`, and rollback does `dest.unlink(missing_ok=True)` with **no** `os.replace(None, dest)`. Test 2 covers the happy rollback of this, but spec the `missing_ok=True` (or guarded unlink) explicitly so the implementer doesn't `os.replace(None, ...)`.

6. **Justify the `os.replace` switch as *in-scope*, and lock the `import os` claim.** The primitive switch (Option A) is **not** scope creep — it is load-bearing: backup-aside + restore *require* an atomic same-fs rename, and `shutil.move`'s documented Windows copy2+unlink overwrite fallback is exactly the within-file tear the slice must also fix (verified: `output_path` holds `.tmp_<ts>/`, `.bak_<ts>/`, and `dest` all as direct children, so same-fs holds by construction — the same-fs claim at line 49 is correct). Keep Option A. One nit to fix in the spec: `loader.py` does **not** currently `import os` (confirmed — only `shutil`, `datetime`, `pathlib`, `typing`, `pandas`, `logging`); the plan says "add `import os`" (line 49) — correct, ensure it lands and that `mypy`/`ruff` see it used.

**Sizing / completeness check:**
- **Single slice — OK, no split needed.** File-local (`loader.py` + `test_loader.py` + 4 doc lines), no contract/signature/caller change (verified at the two call sites), lands vertically complete with reproduce-first regression tests. Well within one session. The `bug` tag's reproduce-first discipline is satisfied (new tests capture the torn-output state as a failure, then the fix makes them pass) — make sure the spec **writes the failing tests first** against the unchanged code to prove they fail for the right reason, per the `bug` discipline in WORKFLOW.md.
- **No new debt** — docs set is correct and complete: `CLAUDE.md:114` line, `DECISIONS.md` (top-append), `ARCHITECTURE_TREE.md:20` (the line currently over-claims "atomically commits so a mid-write failure leaves existing output intact" — refine to name the backup/restore mechanism honestly), `ROADMAP.md ## Later` for the stale-unmanaged-output gap (confirmed `## Later` exists, is human-owned — keep the note to one line). No new indexed file → tree-gate is a refine-in-place, correct.

**Harness impact:** None new. Fully inside existing LIVE dimensions — **data-and-persistence (data integrity)**, **reliability-resilience**, **testing**. No new STANDARD, agent, or gate. One optional Stage-9 candidate (do **not** gate this slice on it): the "atomic dir-commit = per-file backup-aside + ordered rollback + restore-before-`finally`-cleanup" pattern is a reusable invariant — if a second atomic-commit surface ever appears, promote it to `docs/claugentic-INVARIANTS.md`; for now a `DECISIONS.md` line suffices.

### Resolution _(orchestrator)_

All 6 plan-reviewer required changes + yagni's trims folded. The Spec below is **authoritative** where it differs from the draft Approach.

1. **Pin iteration (PR#1):** commit iterates a **materialized `sorted(tmp_dir.iterdir())` list** built before the loop — deterministic order, immune to the loop's own filesystem mutations.
2. **Restore-before-cleanup invariant (PR#2):** rollback lives **inside** `_commit_staged`'s `except`, runs to completion, then **re-raises**; `save_all`'s `finally` (which `rmtree`s `.bak_<ts>/`) therefore only runs **after** restore is done. Stated in the docstring **and** an acceptance criterion. Happy path still `rmtree`s `.bak_<ts>/` (stale-but-valid superseded originals).
3. **Robust test injection (PR#3):** the `os.replace` side-effect classifies each call **by path role** — *promote* (`src` parent is the staging dir), *backup-aside* (`dst` parent is the backup dir), *restore* (`src` parent is the backup dir) — and raises only for a **specific target entity** on the chosen role; everything else delegates to the **real** `os.replace`. Failure is pinned to an entity by **sorted order**, never call-count, and each test **asserts the injected failure actually fired**.
4. **Backup-aside-failure test added (PR#4):** a dedicated regression test injects failure on a **backup-aside** move and asserts all prior files restored + the failing file's `dest` untouched.
5. **New-entity path is a checked criterion (PR#5):** when `dest` doesn't pre-exist → **no backup**, `applied` records `(dest, None)`, rollback uses `dest.unlink(missing_ok=True)` and **never** `os.replace(None, dest)`.
6. **`os.replace` kept + `import os` added (PR#6, yagni KEEP):** in-scope (atomic same-fs rename is *required* by backup/restore; also fixes the latent Windows copy2+unlink within-file tear). `loader.py` gains `import os`; `shutil` stays (used by `rmtree`).
- **Reproduce-first (`bug` discipline):** write the new failing tests against the **unchanged** loader first, confirm they fail for the *right* reason (torn output), then apply the fix.
- **yagni — backup-dir kept deliberately, not defaulted:** a hidden `.bak_<ts>/` is chosen over an in-output sidecar (`dest.csv.bak_<ts>`) because it (a) is symmetric with the existing `.tmp_<ts>/` convention, (b) keeps backups **out of the output namespace** (a transient sidecar would briefly appear in the very dir we're keeping consistent, and survive a kill), and (c) cleans up in one `rmtree` call. Coin-flip acknowledged; choice is deliberate.
- **yagni — test fold:** the "preserve prior" and "drop new" assertions are **folded into one mixed-fixture test** (some entities pre-existing, some new) rather than two near-duplicate tests.
- **yagni — DECISIONS:** one dated line, not a defect essay.
- **Production structure:** **one** new private method `_commit_staged(staged_files, backup_dir)` with rollback **inline** in its `except` — no `_promote`/`_backup` seams (the role-based `os.replace` patch makes tests robust without them).

_Same-model review (Opus family); honest — independence of role + clean context, not model._

---

## Spec _(Stage 4 — one slice)_

### Plain-English (read first)
- **What this builds:** makes writing the output files genuinely all-or-nothing. Today, if the write fails partway (a CSV open in Excel, a full disk), you can be left with **some files updated and some stale** — a silently mismatched roster shipped to SpacesEDU with nobody watching. After this, **any** mid-write failure leaves your existing output **exactly as it was**, and the run fails loudly so an operator can fix the cause and re-run.
- **What "done" means for you:** a normal run produces **byte-for-byte the same files as today** (SD74 proves it). Only the *failure* case changes — and new tests reproduce the old "torn output" first (proving the bug), then prove the fix removes it. All gates green.
- **What you're accepting:** during the commit there's briefly a hidden `.bak_<timestamp>/` folder (auto-removed). On a failure the run aborts with the **original** error and delivers nothing partial. On a truly catastrophic failure (the *restore itself* also fails — dead disk), it stays **best-effort**: it surfaces the real error rather than fake success. You're choosing **Option A** (atomic `os.replace` + per-file backup/restore).

### In-scope dimensions
`data-and-persistence` (integrity) · `reliability-resilience` · `testing` · `maintainability-structure` (light). Non-negotiables: fail loudly · single source of truth · **SD74 byte-identical**.

### Files & changes (one slice)

**`src/etl/loader.py`**
- Add `import os` (top, alphabetical with existing imports). `shutil` stays (used by `rmtree`).
- **Rewrite `save_all`'s commit phase.** Keep staging unchanged (`_write_csv(..., staging=True)` into `.tmp_<ts>/`). After staging, build `staged_files = sorted(tmp_dir.iterdir())` and call `self._commit_staged(staged_files, backup_dir)` where `backup_dir = self.output_path / f".bak_{timestamp}"`. The outer `finally` `rmtree`s **both** `tmp_dir` and `backup_dir` with `ignore_errors=True`. Remove the now-redundant bare `except: rmtree(tmp_dir); raise` (the `finally` already cleans; `_commit_staged` owns rollback + re-raise).
- **Add `_commit_staged(self, staged_files: list[Path], backup_dir: Path) -> None`:**
  - `applied: list[tuple[Path, Optional[Path]]] = []` (dest, backup-or-None), in apply order.
  - For each `tmp_file` in `staged_files`: `dest = self.output_path / tmp_file.name`; `backup = None`; **if `dest.exists()`** → `backup_dir.mkdir(parents=True, exist_ok=True)`; `backup = backup_dir / tmp_file.name`; `os.replace(dest, backup)` (move existing aside). Then `applied.append((dest, backup))` **(record BEFORE promote → covers the in-flight file)**. Then `os.replace(tmp_file, dest)` (promote).
  - `except Exception:` iterate `reversed(applied)`; per file in a `try/except OSError` (so one restore failure logs `logger.error(...)` and does **not** abort the rest or mask the cause): `dest.unlink(missing_ok=True)`; `if backup is not None: os.replace(backup, dest)`. After the loop, **`raise`** (re-raise the original).
  - Docstring states the **load-bearing invariant**: rollback completes inside this `except` *before* the caller's `finally` removes `.bak_<ts>/`; `os.replace` requires same-fs, guaranteed because staging/backup/dest are all children of `output_path`.
- Update the class docstring + `save_all` docstring to describe the backup-and-restore guarantee accurately (replace the current "moved into the output directory only after every file writes" wording, which omits the per-file backup/rollback).

**`tests/test_loader.py`** — rework `TestAtomicWriteRollback`. Add a module-level (or class) helper that builds an `os.replace` side-effect classifying by path role and failing a target `(role, entity)` once, recording that it fired:
```
def _replace_side_effect(*, fail_role, fail_entity):
    real = os.replace
    state = {"fired": False}
    def side_effect(src, dst):
        src, dst = Path(src), Path(dst)
        role = ("promote" if src.parent.name.startswith(".tmp_")
                else "backup" if dst.parent.name.startswith(".bak_")
                else "restore" if src.parent.name.startswith(".bak_")
                else "other")
        target = (src.name if role == "promote" else dst.name if role == "backup" else None)
        if role == fail_role and target == f"{fail_entity}.csv":
            state["fired"] = True
            raise OSError(f"Simulated failure on {fail_role} of {fail_entity}")
        return real(src, dst)
    return side_effect, state
```
Tests (sorted commit order is `Family, Staff, Students`):
1. **`test_mid_commit_failure_preserves_prior_and_drops_new`** *(NEW — the called-out gap; mixed fixture)* — pre-populate `Students.csv="orig-students"` and `Staff.csv="orig-staff"`; **`Family` is new**. Inject `fail_role="promote", fail_entity="Students"` (last). Assert: `OSError` raised; `state["fired"]`; `Students.csv` reads `orig-students` **and** `Staff.csv` reads `orig-staff` (both restored from backup); `Family.csv` does **not** exist (new → rolled back to absent); no `.tmp_*` and no `.bak_*` remain.
2. **`test_backup_aside_failure_preserves_prior_output`** *(NEW — PR#4)* — pre-populate **all three** with `orig-*`. Inject `fail_role="backup", fail_entity="Students"`. Assert: raised; `state["fired"]`; all three read their `orig-*` (Family/Staff promoted then rolled back, Students' `dest` never touched); no temp/backup dirs.
3. **`test_rollback_preserves_existing_output`** *(retained, repointed)* — pre-populate `Students.csv="original content"`; inject `fail_role="promote", fail_entity="Family"` (first). Assert: raised; `state["fired"]`; `Students.csv` == `original content` (never reached); no dirs.
4. **`test_rollback_cleans_up_staging_and_backup_dirs`** *(retained, extended)* — on any injected failure, assert **no `.tmp_*` and no `.bak_*`** remain (was: `.tmp_*` only).
5. **`test_successful_save_all_writes_all_files`** *(retained, extended)* — happy path: all three files exist, hold the **new** values, and **no `.tmp_*`/`.bak_*`** remain (folds the old `*_leaves_no_tmp_dir`).
6. **`test_successful_save_all_overwrites_existing`** *(NEW — happy overwrite path)* — pre-populate originals, run `save_all` with new content, assert every file now holds the **new** value and no temp/backup dirs remain (proves the atomic overwrite + cleanup).

Drop the old `move_fail_on_third` call-count test and the `shutil.move` patches (superseded by role-based `os.replace` injection).

**`CLAUDE.md`** — one dense line near the loader/atomic-write notes: `DataLoader.save_all` commit is **backup-and-restore atomic** — each existing target is moved into `.bak_<ts>/` then the staged file promoted with `os.replace`; any mid-commit failure rolls back (restore originals / remove new files) so the output dir is **never left torn**; `os.replace` not `shutil.move` (atomic same-fs overwrite, fixes the Windows copy2+unlink tear).

**`docs/claugentic-DECISIONS.md`** — top-append one dated line (yagni-trimmed): chose `os.replace` + per-file `.bak_<ts>/` backup/restore for a true all-or-none `save_all` commit; supersedes the per-file `shutil.move` loop, which was non-atomic across files **and** (Windows overwrite) within a file; rollback restores before `finally` cleans `.bak_`; SD74 byte-identical.

**`docs/claugentic-ARCHITECTURE_TREE.md:20`** — refine the `loader.py` line so the `save_all` clause names the backup/rollback mechanism honestly (it currently claims "atomically commits so a mid-write failure leaves existing output intact" — true only after this fix).

**`docs/claugentic-ROADMAP.md` `## Later`** — one concise human-owned line: *stale unmanaged output files* — `save_all` only manages files named in `outputs`; a CSV from a prior run whose entity was later dropped from `enabled_entities` persists in the output dir (could ship a stale entity). Pre-existing, orthogonal to the atomic-commit fix; decide whether to prune-on-commit. (Discovered while planning 0007.)

### Acceptance criteria
- `save_all` leaves the output dir **exactly as before the call** on any commit-phase failure (prior files at prior bytes; would-be-new files absent) — proven by tests 1–3.
- Rollback runs **inside** `_commit_staged`'s `except` and **before** `save_all`'s `finally` removes `.bak_<ts>/` (restore-before-cleanup invariant) — docstring + tests 1–2.
- New-entity commit takes **no** backup and rolls back via `unlink(missing_ok=True)`, never `os.replace(None, …)` — test 1.
- Per-file commit is atomic (`os.replace`); no half-written destination on overwrite — happy overwrite test 6.
- No `.tmp_*`/`.bak_*` dir survives success **or** failure — tests 4–6.
- Each failure-injection test asserts the failure **fired** on the intended file.
- No public signature change; callers (`pipeline.py:356`, `02_Convert.py:351`) untouched.

### Gates (all green)
Full `pytest --cov` (80%) · **SD74 snapshot byte-identical** · `ruff check` + `ruff format --check` · `mypy src/ --exclude 'src/ui'` (confirm `os` is seen used) · `bandit -r src/ -q` · `make validate-config` · `scripts/claugentic-check_architecture_tree.py`.
