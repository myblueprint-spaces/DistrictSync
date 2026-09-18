# 0050 — Output-folder pre-flight: stop misattributing an output fault to the input folder

- **Status:** Spec'd — awaiting user approval (Stage 5)
- **Resumable from:** Stage 5 approval gate. Panel (2b) folded in; gate (Stage 3) returned
  CHANGES REQUIRED × 8, all applied — see `## Review`.
- **Blockers:** none
- **Flags:** none
- **Disposition at close:** single slice; done when it lands.
- **Roadmap item:** adjacent to the 2026-09-17 "mapped network drive fails the nightly silently" entry in `docs/claugentic-ROADMAP.md` — this plan is **neither** of that entry's two named halves (see Non-goals).
- **References:** `docs/claugentic-ARCHITECTURE_TREE.md` · `docs/claugentic-DECISIONS.md` · commit `0fbdb9c`

## Problem

Convert runs the entire ETL before it ever touches the output folder, then fails with copy that
blames the *input* folder.

- `convert_job` checks only that the configured output folder is **non-blank**
  (`src/ui_flet/screens/convert.py:259`), and the view gate `can_run_convert`
  (`src/ui_flet/convert_output.py:88`) takes `output_dir_set` as a bare bool. Neither checks
  reachability or writability.
- The first real contact is `DataLoader(str(output_dir))` at `src/ui_flet/screens/convert.py:324`,
  **after** load → transform → integrity gate → anomaly check. `DataLoader.__init__` calls
  `ensure_directory` → `Path.mkdir(parents=True, exist_ok=True)`
  (`src/etl/loader.py:60`, `src/utils/helpers.py:109`).
- Any `OSError` there routes to `_on_error` (`src/ui_flet/screens/convert.py:778`) and renders
  `convert_error_copy()` (`src/ui_flet/convert_result.py:214`), whose detail reads *"Check that your
  input folder holds this district's MyEd BC extract files"* — wrong in every reproduced case.

Three failures reproduced on released v3.21.0 against SD51's real drop (80k+ rows, seconds of wasted
work each time). **Raise sites corrected** from the first report:

| cause | error | where it actually raises |
|---|---|---|
| unmapped / unreachable drive letter (`Z:\…`) | `FileNotFoundError` WinError 3 | `DataLoader.__init__` → `ensure_directory` |
| path over 260 chars | `FileNotFoundError` (206 as reported; a 303-char path measured here gave 3) | `DataLoader.__init__` → `ensure_directory` |
| present but not writable | `PermissionError` WinError 5 | `save_all` staging `.tmp_<ts>_<uid>/` |

The first two blow up at loader *construction*, not in `save_all`. All three are post-ETL. The exact
WinError for row 2 does not affect the design — both are `FileNotFoundError` out of `mkdir`.

**A FOURTH output-folder failure is already documented in this repo** and is not in the reproduction
list: an output CSV held open in Excel makes `os.replace` raise during commit — *"surfaced as a calm
card and an empty log"* (`src/ui_flet/screens/convert.py:176`). It renders the same input-folder copy.

**The existing validators cannot cover this.**
`filepicker.validate_output_dir` is pure-structural (parent exists, leaf is not a file) and would
catch rows 1–2 but not row 3. `filepicker.check_writable` uses `os.access(W_OK)`, which the repo's
own test skips on Windows — *"POSIX permission bits don't gate os.access on Windows"*
(`tests/test_ui_flet_filepicker.py:90`) — i.e. it is documented-blind on the only platform where all
three were reproduced. It also has **zero production callers**.

**The scheduled/CLI path carries a second bug.** `run_pipeline` builds `DataLoader(output_path)` at
`src/etl/pipeline.py:826`, so it *does* fail before extraction — but `FileNotFoundError` is
classified `RunErrorCategory.CONFIG` by `_classify_error_category`, and `PermissionError` falls to
`UNKNOWN`. An unreachable output folder records a nightly failure as a **config** problem. This is
the SD51 shape: a mapped drive is per-logon-session, so `Z:\` is least likely to exist in a scheduled
task's session.

## Goals / Non-goals

- **Goal:** refuse an unusable output folder **before any ETL work** on the Convert path.
- **Goal:** give that refusal its own bounded, category-only copy naming the **output** folder, and
  route it to the fix.
- **Goal:** stop an output fault reaching the user as input-folder or mapping-folder copy on **any**
  surface — including the write-time faults a pre-check structurally cannot see (Excel lock, a drive
  that drops mid-run).
- **Goal:** give `run_pipeline` the same pre-check and an honest bounded category for support.
- **Non-goal — surfacing the new `output` category on Home / Run History.** Measured: `error_category`
  is written by `pipeline.py` and `store.py` and **read by no user-facing surface**;
  `classify_latest_reason` keys on `status` (`home_status.py:511`) and renders the undifferentiated
  "hit a problem". So the pipeline half of this plan helps **support and the log — not the admin's
  morning.** Stated plainly rather than framed as a user-facing win. ROADMAP line added.
- **Non-goal — pick-time resolution of a mapped drive letter to its UNC target** (`WNetGetConnection`).
  Half (1) of the ROADMAP entry; touches three picker surfaces and a persisted `config.json` value.
- **Non-goal — checking reachability against the task PRINCIPAL at setup time.** Half (2) of the same
  entry. This plan's pre-check runs *inside the task's own process*, so a scheduled run reports its
  own fault honestly — but nothing warns the admin at pick time. **This plan does not make network
  storage handled** and must not be described as doing so.
- **Non-goal — the deliver-from-disk affordance on the same broken folder.** `deliverable_files` is
  total by design, so an unreachable folder collapses to the empty set and the deliver action simply
  **disappears** with no copy at all. Seen, not missed; after this lands it is the last surface where
  an output fault is unexplained. ROADMAP line.
- **Non-goal — showing the configured path in the verdict banner.** `docs/claugentic-PRODUCT.md`
  states as an absolute that the admin is never shown a filesystem path, while the code already
  carves out app-owned configured paths twice on this screen. Resolving that contradiction is a
  durable-doc decision, not a rider on a bugfix. The path reaches the admin via the **caption**
  instead (see Approach), which already shows it today.
- **Non-goal:** changing `convert_error_copy`'s wording, or the Setup/Settings pickers.

## Approach

### 1. One check, in the loader

`src/etl/loader.py` gains a module-level `output_target_problem(path: str) -> str | None` — `None`
when the folder is usable, else a **log-only** free-text reason. It lives beside `DataLoader` because
it must stay in step with how the loader actually uses the directory (`ensure_directory`, then
`.tmp_<ts>_<uid>/` staging); a check elsewhere could drift into a false green. SRP holds — both
change for the same reason. It does not live in `filepicker.py`: that module imports `flet`, and
while a deferred import is technically possible (`job_runner.py:241-243` does exactly that), the
barrier here is the **layering rule** — the ETL layer must not reach into a UI module.

**One `try`, not a five-step ladder.** `Path.mkdir(parents=True, exist_ok=True)` re-raises
`FileExistsError` when the leaf exists and is not a directory (verified on this machine), so a
separate not-a-file step is subsumed, and a separate `resolve()` catch is subsumed by the same
`except`. The distinct reason strings a ladder would buy are worthless for a value that is log-only
and never parsed — the `WinError` is already self-describing.

```
blank                      -> reason
try: resolve -> mkdir(parents=True, exist_ok=True)
              -> reap stale .dsync_probe_* (best effort)
              -> mkdtemp(prefix=".dsync_probe_", dir=resolved)
except (OSError, ValueError) as exc -> reason carrying exc
rmdir(probe)               # failure warns; still usable
return None
```

**The reap is not optional.** Every other leftover class in an output folder has a reaper at a chosen
age (`.tmp_*` at 7 days, `.bak_*` at 1 hour, `loader.py:22`/`:29`). A `.dsync_probe_*` left by a
failed `rmdir` would have **none, at any age** — one stray dir per failed run, unbounded, on exactly
the flaky network shares this targets. Reaping the glob before creating a new probe makes it
self-healing.

**What the probe proves, precisely.** It proves *subdirectory creation* in the output folder — which
is where `save_all` stages (`loader.py:101`). It does **not** prove writing a file inside that
staging dir, nor `os.replace` over an existing `Students.csv` held open by Excel (`loader.py:221`).
The earlier draft's claim that a check performing its own prediction "cannot return a false green"
was overstated; it is corrected here and covered by step 3 below rather than by a bigger probe.

**Verified safe against every sweep and glob** (read, not assumed): `.dsync_probe_*` is invisible to
`_reconcile_output_dir`'s `.bak_`/`.tmp_` branches (`loader.py:154`/`:173`), `detect_stale_outputs`
and `archive_stale_outputs` (`*.csv`), `upload_csvs` (`*.csv` + manifest), and
`convert_output._top_level_csvs`. A probe dir vanishing mid-`iterdir` yields `is_dir() == False`
rather than raising.

### 2. Call sites — gated on `not dry_run`

**The invariant is: before the first output-dir contact AND before any ETL work.** Those coincide in
`run_pipeline` and do **not** coincide in `convert_job` — stating it as "just before `DataLoader`"
would be actively misleading there, since Convert's `DataLoader` sits *after* the whole ETL. Both
positions are named explicitly:

- `run_pipeline` — immediately before `DataLoader(output_path)` at `src/etl/pipeline.py:826`, which
  is after `load_config` (`:804`) and before `load_data` (`:838`). The input-dir check at `:774`
  still wins. This makes "on the CLI this is a no-op" true — the output dir is created at exactly
  the point it is today — and a bad `--sis` still exits at `:815` having created nothing.
- `convert_job` — immediately after the existing blank-output guard (`convert.py:260`), i.e.
  **before `config.to_raw_dict()` and `_read_gde_bytes`**, NOT at its `DataLoader` line (`:324`).
  Placing it at `:324` would satisfy a naive reading of "before the loader" while leaving Goal 1
  unmet and every proposed test still green.

**Acceptance criterion pinning this:** on a refused Convert, `run_transform` is never called.

**`dry_run` skips the check entirely.** Every write in `run_pipeline` is behind `if not dry_run`
(`pipeline.py:887`/`894`/`931`); a preview never calls `save_all`, so requiring writability would be
a new gate on a path that needs none — and a failed probe cleanup would leave a directory on a run
advertised as writing nothing. This also resolves the **third `run_pipeline` caller** the earlier
draft missed: `creator_gate_job` (`job_runner.py:250`) passes `dry_run=True`, and without this gate
an output fault would have surfaced on the mapping-creator as *"This district's mapping can't be used
as it stands."* (`config_editor.py:145`) — the same misattribution bug on a new screen. Gated, that
path is untouched and `config_editor.py` needs no change.

**The blank-output `ValueError` stays exactly as it is** (`convert.py:260`). D10 makes an unset folder
a *gate bug*; it must keep failing loudly. The pre-flight is consulted only for a folder that IS set.

### 3. The write-time catch — what a pre-check structurally cannot see

A pre-check closes the "unusable at t=0" window. It cannot close the Excel lock, a drive that drops
mid-run, or a create-files-denied ACL. So both call sites additionally wrap the **write** in
`except OSError` and route it to the same output-folder outcome.

`OSError` **only** — `save_all` also raises `ValueError` for a missing field-map column
(`loader.py:273`), which is a DATA fault and must keep propagating unchanged.

**Mechanism, named (not left to the implementer).**

- *Pipeline, pre-check:* `_record_early_failure(..., category=RunErrorCategory.OUTPUT.value,
  dry_run=dry_run)` then `sys.exit(1)` — the established early-exit sink, re-raised through the
  existing `except SystemExit: raise` guard so the generic sink cannot double-record.
- *Pipeline, write-time:* wrap `loader.save_all(...)` in `except OSError as exc: raise
  OutputWriteError(...) from exc`, a new `RuntimeError` subclass carrying
  `category = RunErrorCategory.OUTPUT.value`. `_classify_error_category` gains **one** branch
  beside the existing `isinstance(exc, DeliveryIntegrityError): return exc.category` (`:698`) —
  the same "the exception carries its own bounded category" pattern, reusing the generic sink at
  `:1000`. **No third store sink.** This is a correction to the earlier draft, which claimed
  `_classify_error_category` would be untouched: the `FileNotFoundError → CONFIG` branch is
  untouched, but a new sibling branch is required and is the honest minimum. (That draft's stated
  reason for avoiding it — "serves other callers" — was also wrong: it has exactly one caller.)
- *Convert, both:* return `ConvertResult(status=OUTPUT_FOLDER_UNUSABLE)`. `RunErrorCategory.OUTPUT`
  is **pipeline-only**; it never reaches a Convert record, because Convert writes none (below).

**Neither Convert path writes a run record.** The governing rule is `_record_manual_run`'s own
docstring (`convert.py:526-530`), not the `NO_INPUT` precedent the earlier draft cited loosely: it
records *"every run that either produced output or was REFUSED by the delivery gate — those look
like a normal night from the outside."* A pre-flight refusal is explicitly the `NO_INPUT` shape
("you picked the wrong folder", admin watching the surface). A write-time `OSError` also produces
**no** output — `save_all`'s backup-and-restore rollback leaves the output dir exactly as it was —
and is equally watched. Recording either would turn Home's verdict red about a *manual* button
press while the nightly may be perfectly healthy. Note this is a real asymmetry with the
delivery-integrity refusal two call sites away, which *does* record (`convert.py:294-304`) —
because that one is not watched in the same way.

**This reverses a documented deliberate behaviour.** `convert.py:318-323` currently states *"A
`save_all` failure PROPAGATES (fail-loud)"*. After this slice that is true of `ValueError` and false
of `OSError`. The comment must be updated in the same change — a stale comment asserting the
opposite of the code is exactly the debt this harness forbids — and `_WRITE_IN_FLIGHT`'s `finally`
must still clear on both paths.

### 4. Convert surfaces it as a classified RESULT

New `ConvertStatus.OUTPUT_FOLDER_UNUSABLE`, mapped by `summarize` to `Verdict.FAILED` + fixed copy,
following `NO_INPUT` / `INCOMPLETE_ROSTER`. It inherits `summarize`'s three enum-iterating sweeps, so
the copy can neither be forgotten nor leak. Rejected alternative: a `GateRefused`-style exception
caught in `_on_error` — ~6 lines smaller, but it puts copy *selection* inside a closure in
`screens/convert.py`, which is coverage-omitted and no sweep would ever see. It also renders the
`ErrorCard`, the product's never-crash floor, which reads as *"our software has a problem"* where
this should read *"your setup has a problem."*

**Copy** (zero-arg, no path, no exception text):

> **We couldn't save to your output folder**
> DistrictSync couldn't write to your output folder, so nothing was saved and your existing files
> were not changed. Check the output folder in Settings — if it's on a network drive or a shared
> folder, make sure you can still open it. Then try again; if it keeps failing, the Help page has our
> support contact.

**"nothing was saved", not "nothing was converted"** — one string now serves two paths, and on the
write-time path the conversion *did* run; only the save failed. "Nothing was converted" would be
false there and would contradict its own headline. "Saved" is true on both.

"save to" over "use" names the action that failed. The network/shared-folder clause is **coaching,
not detection** — it inspects nothing and branches nowhere, so it does not reintroduce the code the
owner declined; it is widened past "network drive" so it does not bias diagnosis toward the one cause.
**Flagged at the approval gate as strikeable.**

**Routed to the fix.** `DESIGN_SYSTEM.md` principle 5 requires a failed band to offer the concrete
fix; Convert renders every outcome with an empty `trailing` slot (`convert.py:955`) and so has never
met it. This status gets an **outlined** button in that slot — outlined, not filled, because the
screen's one filled primary is Convert. Three constraints, all house rules:

- Label **"Open Setup"**, not "Open Settings" — the destination id is `"setup"`, the rail item reads
  **Setup**, and the existing precedent uses "Open Setup". A button naming a rail item that does not
  exist is a worse dead end than no button.
- Rendered **only** `if on_navigate is not None` — the shell injects it, and an un-injected handler
  would make this a dead click.
- Built via `components.secondary_button` (`components.py:226`), never hand-rolled.

Scoped to this status only; retrofitting the other outcomes is a separate change.

**The caption stops contradicting the banner.** `resolved_output_caption` (`convert_output.py`) is
built once at `convert.py:661` and says *"Files will be written to `<path>` — change it in Settings."*
After a refusal the admin would read that promise in the same viewport as the band disproving it —
a direct hit on the product's "never assert a state you didn't check" bar. The pure function gains a
refused branch, and `_render_result` reassigns `output_caption.value` (the control is a closure local,
so this is a value change, not new wiring). This is also how the admin learns *which* folder, without
putting a path in the banner or reopening the `PRODUCT.md` absolute.

**The caption must RESET.** Because it is built once, a refusal's wording would otherwise persist
over a later *successful* run on the same mount — the same class of stale assertion, inverted. Every
render path sets it, not just the refusal branch. Pinned by a test.

### 5. Retire `check_writable`

Deleted, with its three test rows. It is provably callerless, documented-blind on Windows, and
superseded by the function this slice adds — and this is the one moment the deletion is cheap to
justify. Keeping it and adding a docstring warning would be three artefacts where zero will do.

**Its doc fan-out is FOUR live references, not two** (the earlier draft under-counted): the module
docstring bullet `filepicker.py:58`, the inline note `filepicker.py:87`, the design precedent
`convert_output.py:60`, plus `docs/FLET_1.0_CONVENTIONS.md:67` and
`docs/claugentic-ARCHITECTURE_TREE.md:131`. All must move with it.

## Architecture & holistic fit

- **Codebase fit.** The check lands in the ETL layer (`src/etl/loader.py`), consumed by an ETL caller
  and a UI caller that already imports `DataLoader`. No new module, no new layer crossing. The copy
  stays in `convert_result.summarize`, the existing totality-guarded home for every Convert outcome.
- **Product fit.** The job-to-be-done is "convert my roster and tell me the truth when it fails."
  **On Convert**, the admin now learns in under a second which folder is at fault, sees a screen that
  no longer contradicts itself, and gets a button to the fix. **On the nightly**, the gain is
  ops-only — see the first non-goal; the admin's Home copy is unchanged.
- **Quality dimensions:**
  - `reliability-resilience` — fail fast at the boundary; the probe's scope stated honestly; the
    write-time catch covers what it cannot see; `save_all`'s backup-and-restore atomicity unchanged.
  - `product-ux` — bounded zero-arg copy, one plain next step, routed fix, verdict-first, no
    self-contradicting state. `districtsync-design` skill loaded.
  - `observability-ops` — privacy split preserved structurally: free-text reason → log only
    (`_log_run_record`), bounded category → store, zero-arg card copy.
  - `data-and-persistence` — additive enum member; `error_category` is `TEXT` with no CHECK
    (`store.py:75`); no schema change, no `user_version` bump.
- **Future-proofing.** `output_target_problem` returns a string, not a fault enum: one bounded
  message means a taxonomy would have no consumer. `RunErrorCategory.OUTPUT` is accepted as a
  member whose only consumer today is support — stated, not dressed up.

## Affected files

- `src/etl/loader.py` — `output_target_problem` (+ probe reap).
- `src/etl/pipeline.py` — `RunErrorCategory.OUTPUT`; pre-check (`not dry_run`) at 826; `OSError`
  catch around the write.
- `src/ui_flet/convert_result.py` — `ConvertStatus.OUTPUT_FOLDER_UNUSABLE` + `summarize` branch.
- `src/ui_flet/convert_output.py` — `resolved_output_caption` refused branch.
- `src/ui_flet/screens/convert.py` — pre-check; `OSError` catch; banner `trailing`; caption refresh.
- `src/ui_flet/filepicker.py` — delete `check_writable` + its prose reference.
- `tests/test_etl_loader.py` · `tests/test_pipeline_run_store.py` · `tests/test_ui_flet_convert_result.py`
  · `tests/test_ui_flet_convert_output.py` (owns `resolved_output_caption`) ·
  `tests/test_ui_flet_filepicker.py` (drop the deleted class) · `tests/test_ui_flet_render_smoke.py`.
- `docs/claugentic-ARCHITECTURE_TREE.md` — **five lines go stale** (`:19` pipeline, `:20` loader,
  `:123` `convert_result`, `:124` `convert_output`, `:131` filepicker). The pre-commit gate checks
  entry *presence*, not description drift, so this is a manual obligation the gate will not catch.
- `docs/FLET_1.0_CONVENTIONS.md:67` — drops the `check_writable` clause.
- `docs/claugentic-DECISIONS.md` · `docs/claugentic-ROADMAP.md` · `CLAUDE.md` (≤1 dense line).

## Risks & mitigations

- **Output folder created earlier on Convert** → same folder the admin configured; identical on the
  CLI now that the check sits at the `DataLoader` line; `DataLoader` would create it seconds later.
- **A typo'd leaf (`D:\Rosters\Ouput`) is created and reported green, earlier than before** →
  pre-existing (`validate_output_dir` accepts it; `DataLoader` creates it), but the pre-flight is the
  first thing that *actively creates* a wrong folder and reports success. Consciously accepted.
- **Stray probe dir** → distinct prefix, invisible to every sweep/glob (verified), and self-healing
  via the reap. A failed cleanup warns without failing the run.
- **Residual after the write-time catch:** a fault raised outside both wrapped regions still reaches
  the generic sink. Narrow, and stated rather than claimed away.
- **`OSError` catch must not swallow a data fault** → scoped to `OSError`; `ValueError` propagates
  (`loader.py:273`). Pinned by a test.
- **A dead SMB path can block for tens of seconds** → on Convert the pre-check runs inside
  `convert_job`, i.e. on the `JobRunner` worker thread, so it cannot freeze the UI. On the CLI a
  blocking stat is the status quo (`DataLoader` already touches the path there). This is *why* the
  same check is a non-goal on the Settings Save path, where it would block the button.
- **Snapshot/regression** — no transform, no output CSV, no schema change; SD74 snapshot untouched.

## Test strategy

**Inherited guardrails.** Three enum-iterating sweeps go RED until the new `ConvertStatus` has a
`summarize` branch: `test_ui_flet_convert_result.py:218` (totality), `:249` (privacy),
`test_ui_flet_humanization_sweep.py:277`. Adding a `RunErrorCategory` member breaks nothing — the
only sweep over it is a containment check (`test_pipeline_delivery_integrity.py:256`).

**Positive twin discipline** (no vacuous greens): every "refused / nothing ran" assertion pairs with
a positive showing the same fixture converts when the folder is good.

- `output_target_problem`: usable dir → `None`; creatable nested path → `None` + exists; leaf is a
  file → reason; unreachable root → reason; unwritable dir → reason (POSIX `chmod 0o500`,
  `skipif win32` as `test_ui_flet_filepicker.py:90` does); probe removed on success; a **stale probe
  dir is reaped** on the next call.
- `convert_job`: mirrors the direct precedent `test_pipeline_run_store.py:268`
  (`test_empty_output_dir_fails_loud_and_records_nothing`) — unusable folder →
  `OUTPUT_FOLDER_UNUSABLE`, `read_run_records() == []`, input dir provably untouched. Fixture is the
  established real `AppConfig(...).save()` into the autouse `isolated_user_profile`
  (`tests/conftest.py:181`), no monkeypatching.
- **Write-time catch:** `save_all` raising `OSError` → `OUTPUT_FOLDER_UNUSABLE`; `save_all` raising
  `ValueError` → still propagates to `_on_error` (the twin that stops the catch widening).
- `run_pipeline`: unusable output → `SystemExit` 1, stored `error_category == "output"`,
  `"error" not in stored`, free text in the `__DISTRICTSYNC_RUN__` payload. **Plus the dry-run row**
  that matrix requires (`:477-544`): `dry_run=True` records nothing **and does not probe** — the test
  that pins the creator path stays safe.
- `resolved_output_caption`: refused branch asserts it no longer says "will be written"; **positive
  twin** — a success render after a refusal restores the normal caption (the reset rule).
- **Goal-1 pin:** a refused Convert never calls `run_transform` (the test that catches a check
  placed at `convert.py:324` instead of `:260`).
- Render smoke: the new status mounts with its `trailing` control and does not fall to `ErrorCard`;
  and mounts **without** `on_navigate`, rendering no button rather than a dead one.

**Copy is deliberately NOT quoted in any doc.** No parity test watches `convert_result.py`, so
quoting it in `PRODUCT.md` / `PRODUCT_SPEC.md` / `qa-checklist.md` would land in exactly the unpinned
state `CLAUDE.md:273` forbids.

**ROADMAP lines (one line each, not paragraphs):** surface the `output` category on Home / Run
History · re-check the folder when Settings saves it (needs off-thread treatment — a dead SMB path
blocks for tens of seconds) · deliver-from-disk's silent missing affordance · the pre-existing
unpinned `"The conversion couldn't finish"` literal in `PRODUCT.md:195` / `PRODUCT_SPEC.md:149`.

## Decomposition (slices)

- [ ] **Slice 1 (only)** — all of the above. One check, two call sites, one status, one category,
  one caption branch, one banner slot, one deletion, tests, docs. Inside one session.

---

## Review  _(Stage 3 — synthesizer-gate, plan-gate altitude)_

RUNNING AS: Opus 5

- **Verdict:** **CHANGES REQUIRED** (8 items; none re-open the design — R1–R5 are load-bearing text
  defects, R6–R8 are completeness). The approach is right and the 2b panel's feedback is genuinely
  folded in, not name-checked.

**Premises checked against the code (the four the gate was asked to verify, plus five more):**

| claim | result |
|---|---|
| `not dry_run` leaves `creator_gate_job` unaffected | **TRUE** — `job_runner.py:250` passes `dry_run=True`; it is the 3rd of 3 call sites (`main.py:120`, `main.py:594`, `job_runner.py:250`) |
| `save_all` raises `ValueError` for a missing field-map column → the `OSError`-only catch is correctly scoped | **TRUE** — `loader.py:273` `raise ValueError(f"Cannot write {entity_name}.csv — columns missing from output: …")`, reached via `_write_csv` |
| `pipeline.py:826` is after `load_config` and before extraction | **TRUE** — `load_config` at `:804`, `DataLoader(output_path)` at `:826`, `extractor.load_data` at `:838`. Input-dir validation at `:774` still wins, so precedence stays input-dir → config → output-folder |
| `summarize`'s sweeps go red on a new unmapped status | **TRUE** — totality `test_ui_flet_convert_result.py:218` + privacy `:249` iterate `ConvertStatus`; `test_ui_flet_humanization_sweep.py:277` parametrizes it; the guard is `convert_result.py:174` `raise ValueError(f"Unmapped ConvertStatus: {status!r}")` |
| adding a `RunErrorCategory` member breaks nothing | **TRUE** — `test_pipeline_delivery_integrity.py:256` is containment; `_INTEGRITY_FAULT_STATUSES` is fed only by `integrity_fault.category`, never the new member |
| `check_writable` has zero production callers | **TRUE** — the definition, its own banner comment, `convert_output.py:60` prose, tests, 3 docs. Nothing calls it |
| `.dsync_probe_*` invisible to every output-dir sweep/glob | **TRUE** — grepped every `iterdir()`/`glob(` in `src/`: the only output-dir scans are `loader.py:144` (branches `.bak_`/`.tmp_` only), `loader.py:357`, `uploader.py:581`, `convert_output.py:460` — all `*.csv` |
| `error_category` is read by no user-facing surface | **TRUE** — written at `pipeline.py:1000` / `store.py:135` / `convert.py:542`; `classify_latest_reason` keys on `status` (`home_status.py:511`). The non-goal's honesty holds |
| the two ROADMAP halves | **TRUE** — `docs/claugentic-ROADMAP.md:155` names `WNetGetConnection` and the principal-reachability half exactly as the Non-goals describe, incl. its own "do not ship (1) alone" warning |

### Required changes

**R1 — §2's placement rule is self-contradictory, and read literally it defeats Goal 1 on Convert.**
§2's header says both entry points check "immediately **before** constructing `DataLoader`". True at
`pipeline.py:826`; **false for `convert_job`**, whose `DataLoader(str(output_dir))` sits at
`convert.py:324` — *after* `_read_gde_bytes` → `run_transform` → `check_delivery_integrity` →
`compute_anomalies`. The plan's own Problem section says exactly this. So bullet 1's justification —
*"This is the **same relative order Convert uses**"*, the sentence used to **delete** the earlier
draft's ordering-divergence paragraph — is false, and the divergence is real. An implementer who
follows the header literally places the Convert check at ~`convert.py:322` and ships a slice whose
Goal 1 ("before any ETL work") is unmet, with every proposed test still green except the
input-dir-untouched one. Fix: state the shared invariant as **"before the first output-dir contact
AND before any ETL work"**, give the two positions separately (`pipeline.py:826`, which *happens* to
be its `DataLoader` line · `convert.py:~262`, which is **not**), restore a corrected divergence note,
and add an acceptance criterion that the Convert refusal occurs with **`run_transform` never called**
(assert the call, not merely that the input dir is untouched).

**R2 — the pipeline's write-time `OSError` has no stated mechanism, and "`_classify_error_category`
is left alone" cannot hold as written.** `run_pipeline` has ONE failure sink (`pipeline.py:983-1007`)
and it derives the category from `_classify_error_category(e)` at `:1000`. That function's only
site-known-category branch is `isinstance(exc, DeliveryIntegrityError)` (`:698`) — a `.category`
carrier. So a fault "categorised at the site that knows it came from the write" must be one of:
(a) re-raised as a carrier the first branch already matches — today only `DeliveryIntegrityError`,
semantically wrong here; (b) `_classify_error_category` generalised to read `.category` by
duck-type/protocol — **a change to that function, contradicting §3**; or (c) a second
record-and-store call — a **third** store sink, new debt against CLAUDE.md's single-sink rule.
Name which, and update the `pipeline.py` Affected-files line accordingly. Do the same for the
**pre-check**: the test strategy pins `SystemExit` 1 + `error_category == "output"`, which implies
`_record_early_failure(..., category=RunErrorCategory.OUTPUT.value, dry_run=dry_run)` + `sys.exit(1)`
— say so, so no fourth sink is invented (`_record_early_failure`'s `dry_run` is required
keyword-only by design).

**R3 — Convert's record-or-not decision is unstated, and the cited precedent is the wrong one.**
Two call sites away, `convert_job`'s delivery-integrity refusal **does** write a store row
(`convert.py:294-304`, `status="failed"` + bounded category). The test strategy asserts
`read_run_records() == []` and cites `test_pipeline_run_store.py:268` — the D10 *blank-folder
`ValueError`* test, not a status-returning refusal. The rule that actually governs is
`_record_manual_run`'s own docstring (`convert.py:526-530`): record "every run that either produced
output or was REFUSED by the delivery gate… those look like a normal night from the outside";
`NO_INPUT`/`NEEDS_ANOMALY_ACK` write nothing because "the admin is watching the surface where both
are already shown." Under that rule the new refusal writes nothing — **but cite the rule and say so**,
switch the precedent to the nearer `test_pipeline_run_store.py:~255-266` (`NO_INPUT` →
`read_run_records() == []`), state the same for the write-time `OSError` branch (today a `save_all`
failure escapes to `_on_error` and records nothing — the conversion must not silently start or stop
recording), and reconcile with Goal 4 in one sentence: **`RunErrorCategory.OUTPUT` is written by the
pipeline path only; Convert gives support nothing new.**

**R4 — §3 inverts a documented deliberate behaviour without acknowledging it.** `convert.py:~318-323`
states: *"A `save_all` failure PROPAGATES (fail-loud), and the flag is cleared in the `finally`
either way."* Catching `OSError` there turns a stated decision into its opposite. Defensible (a
classified result beats the generic `ErrorCard`), but name it as a reversal, update that comment,
keep `_WRITE_IN_FLIGHT`'s `finally` intact (the new `except` sits beside it, never replacing it), and
record a DECISIONS line. Also make the "residual" risk bullet concrete rather than generic: name
`archive_stale_outputs` and the SFTP leg, which follow `save_all` and are **outside** the wrapped
region.

**R5 — the `check_writable` retirement's doc fan-out is half-counted, and ARCHITECTURE_TREE is absent
from Affected files.** §5 names two prose references; there are **four** live ones:
`filepicker.py:58` (the purity banner comment), `filepicker.py:87`, `convert_output.py:60`, plus
**`docs/FLET_1.0_CONVENTIONS.md:67`** and **`docs/claugentic-ARCHITECTURE_TREE.md:131`** — the last
two are current-state prose that would name a deleted function. (`docs/claugentic-DECISIONS.md:319`
/`:436` are dated historical entries — leave them, and say so.) Separately,
**`docs/claugentic-ARCHITECTURE_TREE.md` appears nowhere in Affected files** while this slice makes
five of its lines stale: `:19` (pipeline), `:20` (loader — gains `output_target_problem`), `:123`
(convert_result — that line **enumerates** the `ConvertStatus` members), `:124` (convert_output — that
line describes `resolved_output_caption`'s two branches), `:131` (filepicker). The pre-commit tree
gate checks file **presence**, not description drift, so nothing catches this. Add the tree with the
five lines named.

**R6 — the new banner action can be a dead click, and its label names a destination the rail does not
have.** `build_convert(page, on_navigate=None)` (`convert.py:555-557`); the house rule is
`if on_navigate is not None:` before adding a routed control (`convert.py:1246`, `:1347`) —
CLAUDE.md's *"absent ⇒ no affordance, never a dead one."* Gate the trailing control the same way and
say the band stands alone without it. Also: the rail item is **Setup**, the destination id is
`"setup"`, and the existing routed precedent reads **"Open Setup"** (`_setup_first_card`) — the
proposed **"Open Settings"** names a rail item that does not exist. Pin one (the copy *body*'s "in
Settings" is fine; it matches `resolved_output_caption`'s existing wording — it is the **button** that
names a destination). Build it with `components.secondary_button` (`components.py:226`, the outlined
tier); the plan's one-filled-primary reasoning is correct — Convert stays filled and re-runnable.

**R7 — `resolved_output_caption`'s signature change fans out further than listed, and the reset rule
is missing.** Its tests live in **`tests/test_ui_flet_convert_output.py`** (`:90`, `:96`, `:100`,
`:381-396`), which is **not** in Affected files — `tests/test_ui_flet_convert_result.py` is a
different module. Add it. Second, and load-bearing: `output_caption` is built **once** at
`convert.py:661` and is a long-lived closure local, so if only the refused branch reassigns `.value`,
the refusal copy **persists over a subsequent successful run in the same mount**. State the reset —
every `_render_result` branch sets the caption (a `_refresh_output_caption()` helper, mirroring the
existing `_refresh_district_note` at `:653`) — and pin it with a test that renders refused → success.

**R8 — one copy string serves two paths with different truths.** "…so **nothing was converted** and
your existing files were not changed." On the pre-check path that is true. On the **write-time catch**
path the conversion *did* run — only the save failed — so after ~40s of spinner the detail contradicts
its own headline ("We couldn't **save** to your output folder"). Reword to **"nothing was saved"**,
which is true on both paths, keeps the string zero-arg, and keeps headline and detail in one frame.
("your existing files were not changed" is verified true on both paths — `save_all` is
backup-and-restore atomic and the pre-check writes nothing.)

### Non-blocking corrections (fold in while editing)

- **§1's "It cannot live in `filepicker.py`, which imports `flet` and so is unreachable from
  `pipeline.py`" is overstated.** `job_runner.py:241-243` reaches `filepicker` from a flet-free module
  via a deferred import, so it *is* reachable. The real barrier is the **layering rule** (the ETL layer
  must not import the UI layer) — the stronger and true reason. Say that instead.
- One sentence in Risks: the Convert pre-check runs on the `JobRunner` worker thread
  (`convert.py:790-796`), so a dead SMB path blocks the *job*, not the UI — the same hazard the plan's
  own Settings ROADMAP line names, and worth stating as already-handled here.

### Sizing / completeness check

- **Slice 1 (only) — ONE slice CONFIRMED, conditional on R1–R3 landing in the Stage-4 spec.**
  Real footprint is ~16 files (6 source + 6 test — R5/R7 add two — + 4 docs), but every edit is small
  and all are bound to a single fault; in line with landed slices here (0038 S4a/S4b, 0044). It
  **lands vertically complete**: no `TODO`, no half-wired status, no debt. The two things that could
  burn the session are the under-specified mechanisms (R2's pipeline categorisation, R3's
  record-or-not) — an implementer would have to re-derive both from `_classify_error_category` and
  `_record_manual_run`'s docstring mid-session. Pin them at Spec and the slice fits.
- **The `check_writable` retirement (§5) is the one genuinely separable rider.** Keep it — it is the
  function a future reader would otherwise reach for instead of `output_target_problem`, and splitting
  it out would strand a callerless, Windows-blind probe next to its replacement. But count its real
  doc fan-out (R5); that, not the 20-line deletion, is its cost.
- **Path (Stage 0): correct.** ~16 files, an ETL↔UI crossing, and three shared contracts touched
  (`ConvertStatus`, `RunErrorCategory`, the failure-classification seam) — full pipeline, not lightweight.
- **Risks + test strategy: stated and adequate.** Positive-twin discipline honoured; the `ValueError`
  twin that stops the `OSError` catch widening is the right pin; the dry-run row protecting
  `creator_gate_job` is correctly identified as required. Add the two acceptance criteria named in R1
  (`run_transform` never called) and R7 (the caption resets).
- **Architecture & holistic fit: genuinely reasoned, not gold-plated.** It names layering and SRP,
  argues module placement against a real alternative, declines a fault taxonomy on YAGNI grounds with
  a stated reason, maps four dimensions to their modules, and — the part that earns it — states the
  product fit **honestly asymmetric** (Convert gains; the nightly gain is ops-only). Its one factual
  slip is corrected above.

### Harness impact

- **None plugin-side.** No new STANDARD, agent, or managed doc; nothing is staged for upstream. All
  four doc touches are repo-local: `docs/claugentic-DECISIONS.md` (R3's recording rule + R4's
  reversal), `docs/claugentic-ROADMAP.md` (the four lines already listed), `CLAUDE.md` (≤1 dense line),
  and **`docs/claugentic-ARCHITECTURE_TREE.md`** (R5 — currently missing).
- **Stage-9 candidate, not a required change:** *"a probe artefact needs a reaper at a chosen age or it
  is unbounded"* — §1's reap reasoning generalises beyond this repo and is a reasonable
  `docs/claugentic-standards/CANDIDATES.md` line at Land.

**Bounding the loop:** R1–R8 are sentence- or line-level edits to the plan plus two Affected-files
additions — none re-opens a design fork. **Applying them as written closes the gate; the next round is
a diff confirmation of these eight, not a re-gate.**


---

## Spec  _(Stage 4 — single slice)_

### In plain English (read this first)

**What this builds.** When you press Convert, DistrictSync checks the output folder *first* —
in under a second, before it reads a single roster file. If that folder can't be reached or written
to, it stops there and tells you so, naming the **output** folder, with a button that takes you to
the screen where you fix it. The same check runs at the top of the scheduled/CLI run. And if the
folder breaks *during* a run instead (a file open in Excel, a network drive that drops), that now
gets the same honest message instead of the one blaming your input folder.

**What "done" means for you.** All four known output-folder failures — unreachable drive, over-long
path, unwritable folder, file locked by Excel — say "we couldn't save to your output folder" and
route you to the fix. None of them blames your MyEd BC extract files. Your existing output files are
never touched in any of these cases.

**What you're accepting.**
1. **The check creates the output folder** (it does the real `mkdir` the loader would do, then
   proves it can create a folder inside). So a run that fails *later* for some other reason now
   leaves an empty output folder behind, and a typo'd folder name gets created and reported fine —
   both were already true a few seconds later; this just moves them earlier.
2. **The nightly half helps support, not your morning.** The new `output` category goes to the log
   and the run store. Home and Run History still say "hit a problem" — nothing reads that category
   today. Making them say "output folder" is a named follow-up, not this slice. Say the word and I
   pull it in.
3. **`check_writable` gets deleted.** It has no callers, and its test is skipped on Windows because
   `os.access` can't see Windows permissions — it is the function that *would* have caught your
   third case and couldn't.
4. **Scope I added beyond your ask** — the write-time `OSError` catch. Without it the Excel-lock
   case (already documented in this repo) and a mid-run drive drop keep showing the input-folder
   copy, so the fix would be half a fix.
5. **One copy decision is still yours** — see the question below.

### Files & changes

| file | change |
|---|---|
| `src/etl/loader.py` | + `output_target_problem(path: str) -> str | None`. One `try`: `resolve` → `mkdir(parents=True, exist_ok=True)` → reap stale `.dsync_probe_*` → `mkdtemp(prefix=".dsync_probe_", dir=…)` → `rmdir`. Blank → reason. Returns a **log-only** reason; `None` = usable. |
| `src/etl/pipeline.py` | + `RunErrorCategory.OUTPUT = "output"`. + `OutputWriteError(RuntimeError)` carrying `.category`. Pre-check before `DataLoader(output_path)` (`:826`), skipped when `dry_run` → `_record_early_failure(..., dry_run=dry_run)` + `sys.exit(1)`. `save_all` wrapped in `except OSError → raise OutputWriteError`. One new branch in `_classify_error_category` beside the `DeliveryIntegrityError` one; `FileNotFoundError → CONFIG` untouched. |
| `src/ui_flet/convert_result.py` | + `ConvertStatus.OUTPUT_FOLDER_UNUSABLE` + its `summarize` branch (`Verdict.FAILED` + the copy below). |
| `src/ui_flet/convert_output.py` | `resolved_output_caption` gains a refused branch (stops saying "will be written"). |
| `src/ui_flet/screens/convert.py` | Pre-check after the blank guard (`:260`) — **before** `to_raw_dict`/`_read_gde_bytes`. `except OSError` around loader-construct + `save_all`; `_WRITE_IN_FLIGHT` `finally` preserved; the `:318-323` "PROPAGATES" comment updated. Banner `trailing` = `secondary_button("Open Setup")`, only when `on_navigate is not None`. Caption set on **every** render path. |
| `src/ui_flet/filepicker.py` | − `check_writable` + its docstring bullet (`:58`) and inline note (`:87`). |
| `src/ui_flet/convert_output.py` (docstring) | − the `check_writable` design-precedent mention (`:60`). |
| docs | `ARCHITECTURE_TREE` ×5 stale lines · `FLET_1.0_CONVENTIONS.md:67` · DECISIONS ×1 · ROADMAP ×4 one-liners · `CLAUDE.md` ≤1 line. |

### Copy (zero-arg, no path, no exception text)

> **We couldn't save to your output folder**
> DistrictSync couldn't write to your output folder, so nothing was saved and your existing files
> were not changed. Check the output folder in Settings — if it's on a network drive or a shared
> folder, make sure you can still open it. Then try again; if it keeps failing, the Help page has
> our support contact.

### In-scope standards dimensions

`reliability-resilience` (boundary fail-fast; probe scope stated honestly; write-time catch;
`save_all` atomicity untouched) · `product-ux` (bounded zero-arg copy, routed fix, verdict-first, no
self-contradicting state) · `observability-ops` (free text → log only; bounded category → store) ·
`data-and-persistence` (additive enum; no schema change, no `user_version` bump).

### Tests to add

`output_target_problem` ×7 (usable · creatable · file-leaf · unreachable · unwritable [POSIX,
`skipif win32`] · probe removed · **stale probe reaped**) · `convert_job` refusal + positive twin +
`read_run_records() == []` + input dir untouched + **`run_transform` never called** · write-time
`OSError` → status / `ValueError` → still propagates · `run_pipeline` unusable → exit 1 +
`error_category == "output"` + no free text in store · **`dry_run=True` neither records nor probes**
· caption refused branch + reset twin · render smoke with and without `on_navigate`.

### Acceptance criteria

1. A refused Convert returns `OUTPUT_FOLDER_UNUSABLE` **without calling `run_transform`**, writes no
   run record, and leaves the input folder byte-identical.
2. All four known causes render the output-folder copy; none renders `convert_error_copy`.
3. `save_all` raising `ValueError` still reaches `_on_error` unchanged.
4. `run_pipeline` with an unusable output folder exits 1 and stores `error_category == "output"`
   with no free-text error; with `dry_run=True` it neither probes nor records.
5. `creator_gate_job`'s behaviour is byte-identical (it passes `dry_run=True`).
6. No surface claims files will be written to a folder just proven unwritable; the claim returns on
   the next successful run.
7. All gates green: pytest + 80% coverage · SD74 snapshot · tree-check · ruff check/format · mypy ·
   bandit · `make validate-config` · `scripts/check_no_emails.py`.
