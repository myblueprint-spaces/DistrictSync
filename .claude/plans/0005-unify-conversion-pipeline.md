# 0005 — Unify the two ETL mechanisms (CLI/wizard `run_pipeline` ↔ ad-hoc Convert page)

- **Status:** Landed (slices 1/2a/2b/3 + Verify fix; all gates green; committed on feat/unify-conversion-pipeline)
- **Roadmap item:** (new — consolidation surfaced while fixing the StudentAttendance BOM bug, 2026-06-22)
- **References:** `docs/claugentic-ARCHITECTURE_TREE.md` · `docs/claugentic-DECISIONS.md` (2026-06-22 entries) · plan 0004 (StudentAttendance)

## Problem

DistrictSync has **two parallel implementations** of the extract→transform→load pipeline:

1. **`run_pipeline`** (`src/etl/pipeline.py`) — used by the CLI **and** the wizard (the wizard schedules the exe via `register_task` and its "Run Test" button calls `run_pipeline` directly: `src/ui/pages/01_Setup_Wizard.py:103,845`). Uses `DataExtractor` → `DataTransformer` → `DataLoader`.
2. **`run_conversion`** (`src/ui/pages/02_Convert.py:167`) — the ad-hoc Streamlit Convert page. **Reimplements** extraction, orchestration, field-ordering, CSV writing, diff, and anomaly detection in the UI layer.

This is the root cause behind the StudentAttendance BOM bug (encoding decision lived in two places). The duplication produces **divergent data for the same input** and violates the CLAUDE.md layer-isolation rule (UI must not hold ETL business logic):

| Stage | `run_pipeline` (canonical) | `run_conversion` (divergent) | Consequence |
|---|---|---|---|
| Extract | `DataExtractor`: byte-inspection encoding (`_detect_encoding`), header-based delimiter, **malformed-row repair** (`_read_repaired`) | `_load_uploaded_file` (`02_Convert.py:46`): naive first-combo loop, `on_bad_lines="warn"` | UI **drops** MyEd rows with an unquoted comma in the trailing `Section` column; **mojibakes** slightly-broken UTF-8 |
| Transform-orchestration | school-year + entity loop, **respects `enabled_entities`** (`pipeline.py:203-206`) | reimplemented loop (`02_Convert.py:207-227`), **ignores `enabled_entities`** | UI can emit a CSV the CLI would not |
| Field order | in `DataLoader._write_csv` (`df[field_order]`, strict) | reimplemented inline (`02_Convert.py:224-227`, keeps extras) | drift risk |
| Write/encoding | `DataLoader.save_all` (atomic, `csv_encoding`) | inline `to_csv` | the BOM bug (now patched at the encoding layer only) |
| Diff / anomaly | `_print_diff` / `_check_anomalies` | `_compute_diff` / `_check_anomalies_ui` (copies) | duplicated logic |

> **Review correction (no school-year-algorithm bug):** the school-year *algorithm* is currently identical in both paths — `02_Convert.py:197-205` is line-for-line equivalent to `pipeline.py:181-192`. The orchestration divergence is (a) the **`enabled_entities` filter** the UI omits and (b) the **indirect** effect of the weaker extractor feeding `determine_school_year` different rows. The spec author should not hunt a school-year-algorithm bug.

## Goals / Non-goals

- **Goal:** one source of truth for each ETL stage — extraction (encoding + repair), transform-orchestration (school-year + entity loop + `enabled_entities` + field-order collection), CSV write/encoding, and diff/anomaly **compute**.
- **Goal:** the Convert page becomes a **thin adapter** — uploaded bytes → shared extract → shared transform → outputs (for display) → shared load — with no ETL business logic in the UI layer.
- **Goal:** a **CLI↔UI output-parity test** that locks identical outputs for identical inputs (would have caught the BOM bug).
- **Goal:** fix the two latent correctness bugs (extraction divergence, `enabled_entities` ignored) as a *consequence* of unifying — not as separate patches.
- **Non-goal:** any change to the SpacesEDU output contract, the transformers, or the YAML configs. **SD74 snapshot must stay byte-identical.**
- **Non-goal:** new UI features or changed UX — the Convert page's outward behavior (upload, preview, quality, diff, SFTP, downloads) is preserved.
- **Non-goal:** refactoring the wizard (already delegates to `run_pipeline`).

## Approach

Decompose `run_pipeline` into **I/O-agnostic ETL-layer stage functions** that both callers share. The CLI keeps its dry-run/diff/quality/SFTP/structured-log wrapper; the UI keeps its Streamlit rendering; the *engine* between them is shared.

1. **Source-agnostic extraction.** Refactor `DataExtractor`'s parsing core (`_detect_delimiter`, `_detect_encoding`, `_read_with_fallback`, `_read_repaired`) to operate on **`bytes`** rather than a disk `Path`. Add an in-memory entrypoint, e.g. `load_from_bytes(sources: dict[str, bytes], file_headers) -> dict[str, DataFrame]`. `load_data(required_files, file_headers)` (disk) becomes a thin wrapper: read each file's bytes, dispatch to the same core. The Convert page passes uploaded `BytesIO` bytes to `load_from_bytes`; **delete `_load_uploaded_file`**. → UI inherits encoding-detection + the repair pass for free.
   - *Care:* `_read_repaired` currently opens with `newline=""` and a text encoding; on bytes it decodes via `io.StringIO(data.decode(encoding, errors))` (or `TextIOWrapper(BytesIO)`). Behavior must stay identical — guarded by extractor tests + SD74 snapshot.

2. **Shared transform-orchestration — in `pipeline.py` (no new module).** Add `run_transform(raw_data, mappings, global_config) -> TransformOutputs` to **`src/etl/pipeline.py`** (the existing "core ETL orchestration" home), where `TransformOutputs` is a 2-field **`NamedTuple`** (`outputs: dict[str, DataFrame]`, `field_orders: dict[str, list[str]]`). It owns: school-year determination, `entity_order` resolution, **`enabled_entities` filtering**, the per-entity loop (source resolution, skip-on-empty-primary, transform), and field-order collection — i.e. exactly `pipeline.py:181-234` lifted into a function. `run_pipeline` calls it; the Convert page calls it (it already imports `extract_required_files` from `pipeline.py`). → kills the orchestration duplication **and** the `enabled_entities` divergence, with no new file/import surface. *(Review change 3/6: a new `orchestrator.py` module + dataclass for one function is speculative SRP with only two callers and no import cycle — earn the module on a real second pressure.)*

3. **Unified load.** The Convert page writes through `DataLoader` for both ordering **and** encoding (today's patch centralized encoding only). For the SFTP temp dir, write via `DataLoader.save_all(outputs, field_orders)` (reuse the existing atomic path — **no new public method** unless the download path genuinely can't touch disk). The download zip keeps containing all CSVs (local convenience); SFTP delivery keeps the zip/standalone split in `upload_csvs` (unchanged). **Two UI behavior changes this introduces — explicit, tested, not silent (Review change 1):** (a) `DataLoader._write_csv` *raises* on a missing field-map column (`loader.py:122-124`) where the UI loop tolerates it today — a desirable fail-loud, but UI-visible; (b) `_write_csv` writes strictly `df[field_order]`, **dropping** extra columns the UI currently keeps (`02_Convert.py:227-228`) — confirm no enabled entity emits extras the download relied on, and the parity lock asserts identical CLI/UI column sets.

4. **Unify the anomaly compute only (not diff).** Dedupe the genuinely-duplicated business logic: one `compute_anomalies(outputs, output_dir) -> list[str]` + a single `ANOMALY_THRESHOLD` constant (currently duplicated at `pipeline.py:33` and `02_Convert.py:29`). CLI logs / UI renders Streamlit warnings over the same compute. **Diff stays per-surface** — it is ~6 lines of row arithmetic wrapped in irreducibly different formatting (the UI stringifies `Previous` to dodge the documented pyarrow `ArrowInvalid` gotcha; the CLI builds `+N`/`-N` print lines); a shared `DiffRow` would fight both surfaces. *(Review change 4.)*

5. **Parity lock.** A test that runs the **same synthetic GDE bytes** through (a) the CLI path (`run_pipeline` → temp output dir) and (b) the UI adapter path (`load_from_bytes` → `run_transform` → `DataLoader`), asserting **identical** output frames *and* on-disk CSV **bytes** — explicitly covering one no-BOM entity (`StudentAttendance`) and one with-BOM entity (mirroring the 2026-06-19 BOM regression test's two-entity assertion). This is the highest-value asset: it would have caught the original BOM bug and permanently locks the two paths.

**Alternatives considered:**
- *New `src/etl/orchestrator.py` for `run_transform`.* **Rejected** (both reviewers): speculative SRP — two callers, no import cycle, `pipeline.py` is already the orchestration home; extracting later is a trivial follow-up if a third consumer appears.
- *Extract a shared `compute_diff`/`DiffRow`.* **Rejected:** mostly per-surface presentation; the shared shape would fight the Streamlit Arrow workaround and the CLI print format. Unify only `compute_anomalies`.
- *Make the UI call `run_pipeline` directly.* **Rejected:** `run_pipeline` is disk-in/disk-out and bundles CLI side-effects (stdout, `sys.exit`, structured logging); the UI needs in-memory frames for live preview. The shared **stage functions** are the right seam, not the CLI wrapper.

## Affected files

- `src/etl/extractor.py` — refactor core to bytes-based (exactly two public entrypoints: `load_data` disk wrapper + new `load_from_bytes`); the bytes-core must reproduce `_read_repaired`'s `newline=""`/decode semantics.
- `src/etl/pipeline.py` — add `run_transform(...)` + `TransformOutputs` (NamedTuple) + `compute_anomalies(...)` + the single `ANOMALY_THRESHOLD`; `run_pipeline` calls `run_transform`; `_check_anomalies` becomes a thin renderer over `compute_anomalies`. **No new `orchestrator.py`.**
- `src/ui/pages/02_Convert.py` — `run_conversion` → thin adapter; delete `_load_uploaded_file` + `_check_anomalies_ui` (delegate to shared `load_from_bytes` / `compute_anomalies`); write via `DataLoader`; keep `_compute_diff` (per-surface) but read `ANOMALY_THRESHOLD` from `pipeline`.
- `docs/claugentic-ARCHITECTURE_TREE.md` — **fix the already-wrong `02_Convert.py` entry (line 87: says "runs `run_pipeline()`" — it runs `run_conversion()`)**; update `extractor.py` (line 17) for `load_from_bytes` and `pipeline.py` (line 19) for `run_transform`/`compute_anomalies`. No new file entry.
- `tests/` — bytes-path extractor tests (incl. malformed-`Section` repair + encoding); `run_transform` unit tests (`enabled_entities`, field-order); CLI↔UI byte-parity test; fail-loud + extra-column tests for the UI write switch; shared anomaly tests.
- `docs/claugentic-DECISIONS.md` — landing entry.
- `tests/` — new: in-memory extractor tests (incl. repair + encoding), `orchestrator` unit tests, CLI↔UI parity test, shared diff/anomaly tests. Existing extractor/pipeline/loader tests stay green.
- `docs/claugentic-ARCHITECTURE_TREE.md` — index `orchestrator.py`; update `extractor.py`, `pipeline.py`, `02_Convert.py` descriptions.
- `docs/claugentic-DECISIONS.md` — decision entry on landing.

## Risks & mitigations

- **SD74 snapshot regression (CLI path).** → The refactor is extract-method (behavior-preserving); SD74 golden-file test is the guard and must stay byte-identical at every slice.
- **Extractor bytes-vs-disk parsing drift** (newline handling in the repair pass; encoding detection). → Mirror existing disk tests as bytes tests; keep the exact pandas calls on `BytesIO`/`StringIO`; SD74 snapshot.
- **UI regression (Convert page is low-coverage).** → The parity test + the existing Playwright UI smoke test; preserve outward behavior; manual `streamlit run` check at Verify.
- **Scope creep into over-engineering.** → `yagni-sentinel` at Plan + Verify; the dataclass stays minimal; no speculative abstraction (only the two real callers exist).

## Test strategy

- **Extractor:** existing disk tests green; ADD bytes-path tests mirroring them, explicitly covering the malformed-row repair and encoding-detection cases (proving the UI path now repairs + detects).
- **Orchestrator:** `run_transform` unit tests — `enabled_entities` filtering, `entity_order`, skip-on-empty-primary, `field_orders` collection.
- **Parity:** same synthetic GDE bytes → CLI temp-dir CSVs vs UI-adapter outputs → assert equal frames + equal on-disk bytes (incl. StudentAttendance no-BOM).
- **Diff/anomaly:** unit tests for the shared compute; CLI + UI renderers exercised.
- **Gates:** full suite, SD74 snapshot, contract, e2e, 80% coverage, ruff/mypy/bandit, tree-check.

## Decomposition (slices)

Re-sliced per plan-review (change 2): Slice 2 was split so the UI never sits in a *shared-transform / private-write* half-merged state. Each slice lands **complete in one ≤1M-context session, no debt** — every slice keeps all gates + SD74 snapshot green.

- [x] **Slice 1 — Source-agnostic extraction.** Refactor `DataExtractor` core to bytes (two entrypoints: `load_data` wrapper + `load_from_bytes`); Convert page uses `load_from_bytes`; delete `_load_uploaded_file`. **Gate:** round-trip a malformed-`Section` fixture through *both* `load_data` and `load_from_bytes` and assert frame-equal (plus encoding-detection bytes tests) — not just SD74. *Lands complete:* UI extraction byte-identical to CLI; row-drop/mojibake divergence gone.
- [x] **Slice 2a — `run_transform` in `pipeline.py`, CLI adopts it only.** Lift `pipeline.py:181-234` into `run_transform` (+ `TransformOutputs` NamedTuple, `enabled_entities` filter, field-order collection); `run_pipeline` calls it; **UI untouched.** *Lands complete + SD74 byte-identical* — pure extract-method on the canonical path (the snapshot-risky step, isolated).
- [x] **Slice 2b — UI adopts `run_transform` + unified load + parity lock (atomic flip).** Convert page calls `run_transform` **and** writes via `DataLoader.save_all` in the same slice, so the UI goes from fully-private to fully-shared engine+write at once. Add the CLI↔UI byte-parity test (incl. a no-BOM + a with-BOM entity). **Explicit acceptance for the two behavior changes:** (a) fail-loud test on a missing field-map column; (b) confirm/assert identical CLI/UI column sets (extras dropped). *Lands complete:* no half-merged window; parity lands next to the parity it creates.
- [x] **Slice 3 — Unify the anomaly compute.** Single `compute_anomalies` + one `ANOMALY_THRESHOLD`; CLI logs / UI renders over it; delete `_check_anomalies_ui`. Diff stays per-surface. Tests: shared-anomaly unit tests. *Lands complete:* anomaly logic single-sourced; UI holds only diff presentation. (Small — may ride in 2b; kept separate for a crisp landing.)

---

## Review  _(filled by plan-reviewer + yagni-sentinel, Stage 3)_

RUNNING AS: Opus 4.x — **same-model review on this run** (the Fable override was unavailable on this account and fell back to the default model; the judge and the builder are the same model family here, so this reduces but does not eliminate shared-blind-spot risk).

**Verdict: CHANGES REQUIRED** (close — the approach is correct and the seam is sound; the failures are sizing/completeness on Slices 2+3 and two unstated behavior changes that must become explicit acceptance criteria, not silent consequences).

### What's right (grounded against the code)
- The **`run_transform` seam is sound.** The CLI side-effects (`sys.exit` on bad input/config at `pipeline.py:139-152`, dry-run/diff/quality prints at `250-263`, structured run-log at `269`) all sit **outside** the `181-234` range being lifted. `transform()` is stateful only via a fresh per-instance `TransformContext` (`transformer.py:24-25`), and both callers already build a new `DataTransformer()` per run — so lifting school-year-determination + `set_school_year` + the entity loop **as one unit** is behavior-preserving. Good.
- **School-year determination already matches exactly today.** `02_Convert.py:197-205` is line-for-line equivalent to `pipeline.py:181-192` (same `.get` defaults, same `rollover_md`/`naming` fallback, same `determine_school_year`/`set_school_year` calls). **The plan's Problem table is slightly misleading here — there is NO school-year-algorithm divergence.** The only school-year risk is *indirect*: the weaker UI extractor feeds `determine_school_year` a different `raw_data` (dropped/mojibaked rows in the `school year` column at `base.py:691-697`). Fix the Problem section so the spec author doesn't go hunting for an algorithm bug that isn't there.

### Required changes (numbered, actionable)

1. **Slice 3 hides two real behavior changes — make them explicit acceptance criteria, not "consequences."** Switching the Convert page to `DataLoader.save_all`/`_write_csv` changes UI behavior in two ways the plan never states:
   - **(a) Fail-loud on missing field-map columns.** `loader.py:122-124` *raises* `ValueError` when any `field_order` column is absent from the frame; today's UI loop (`02_Convert.py:226`) tolerantly keeps only the columns that exist. This is a *desirable* fail-loud improvement (matches the CLAUDE.md "fail loudly" rule), but it is a UI-visible behavior change — a Convert run that previously produced a partial CSV will now error. Add an explicit acceptance criterion + a test for it.
   - **(b) Extra columns are dropped.** Today the UI writes `transformed[ordered + extra]` (`02_Convert.py:227-228`), keeping any non-field-map columns; `_write_csv` writes strictly `df[field_order]` (`loader.py:126`), dropping extras. Slice 3 must (i) confirm no enabled entity currently emits extra columns the UI download relied on, and (ii) the parity lock must assert the CLI/UI column *sets* are identical (this is the whole point). State this in the slice.

2. **Slice 2 is too big and would land in a half-unified intermediate state — split it.** As written, Slice 2 routes **both** `run_pipeline` and the Convert page through `orchestrator.run_transform` in one session. But `run_pipeline`'s loop also collects `field_orders` (`pipeline.py:197,234`) which `save_all` consumes (`pipeline.py:242`) — so moving the loop into `run_transform` forces touching the CLI write path *and* the UI orchestration *and* the SD74 snapshot guard together, while the UI still writes via its old inline `to_csv` path (Slice 3 hasn't happened yet). That leaves the UI with shared-transform-but-private-write — a partially-merged path. Split:
   - **Slice 2a — introduce `run_transform`, CLI adopts it only.** `run_pipeline` calls `run_transform`; UI untouched. Lands complete + SD74 byte-identical (pure extract-method on the canonical path; this is the snapshot-risky step, isolated).
   - **Slice 2b — UI adopts `run_transform` + unified load (merge with current Slice 3).** Convert page calls `run_transform` **and** writes via `DataLoader` in the same slice, so the UI flips from "fully private engine" to "fully shared engine+write" atomically, with the parity lock landing in the same slice that creates parity. This removes the half-unified window and folds the parity test next to the change it locks.

3. **Drop the new `src/etl/orchestrator.py` — put `run_transform` in `pipeline.py` (agree with yagni-sentinel).** The plan's own rejection (line 50) concedes "Reviewer may overrule on KISS grounds" — overruling. There are exactly two callers and no import cycle (`02_Convert.py` already imports `extract_required_files` from `pipeline.py` at line 23). A new module to hold one function + one small result type is speculative SRP; `pipeline.py` is already the "core ETL orchestration" home per the architecture tree (line 19). Keeping it there also means Slice 2a touches one fewer file and the tree needs no new entry. If `pipeline.py` later grows unwieldy, extracting is a trivial follow-up — earn the module on a real second pressure (YAGNI).

4. **Unify only `compute_anomalies`; do NOT extract a shared `compute_diff`/`DiffRow` (agree with yagni-sentinel).** The anomaly *compute* is genuinely duplicated business logic incl. the `ANOMALY_THRESHOLD = 0.20` literal duplicated at `pipeline.py:33` and `02_Convert.py:29` — unify that (single constant, single function). But diff is ~6 lines of row arithmetic (`pipeline.py:344-360` vs `02_Convert.py:116-128`) wrapped in irreducibly per-surface formatting (the UI deliberately stringifies `Previous` to dodge the documented pyarrow/`ArrowInvalid` gotcha — `02_Convert.py:122-126` + CLAUDE.md "Streamlit Arrow gotcha"; the CLI builds `+N`/`-N` print lines). A shared `DiffRow` would force a lowest-common-denominator shape that fights both surfaces. **Revise Slice 4** to unify anomaly-compute + the threshold constant only; leave diff rendering per-surface. This shrinks Slice 4 substantially.

5. **Hold the extractor to exactly two public entrypoints (agree with yagni-sentinel) and state the bytes-core contract precisely.** Slice 1 should land `load_from_bytes(sources, file_headers)` + the existing `load_data` as a thin wrapper over the *same* private bytes-core — no third variant. **Critical correctness detail the plan under-specifies:** `_read_repaired` (`extractor.py:233-262`) opens the file with `newline=""` (so `csv.reader` sees physical rows correctly) and decodes with `(encoding, errors)`; the bytes path must reproduce *both* — decode bytes with the same `encoding`/`errors`, then feed `csv.reader` a `StringIO` (which already yields `newline=""`-equivalent behavior since the string is already decoded). Also `_detect_delimiter` reads via `open(...,'rb').readline()` (`extractor.py:91-92`) and `_detect_encoding` via `read_bytes()` (`extractor.py:114`) — both already operate on raw bytes, so the bytes-core is mostly mechanical, but the spec must pin the `newline`/decode equivalence as an explicit test (round-trip a known malformed-`Section` fixture through *both* `load_data` and `load_from_bytes` and assert frame-equal). Make that the Slice-1 acceptance gate, not just "SD74 snapshot."

6. **Soften `TransformOutputs` to a `NamedTuple` (agree with yagni-sentinel).** It carries two fields (`outputs`, `field_orders`) and no behavior; a frozen dataclass is fine but a `NamedTuple` is lighter and unpacks cleanly at both call sites. Minor — note it in the spec.

7. **The parity test must assert on-disk *bytes*, including the no-BOM rule — keep this, it's the highest-value item.** Confirmed the BOM split is real and centralized (`loader.csv_encoding` at `loader.py:33-43`, `_NO_BOM_ENTITIES = {"StudentAttendance"}`). The parity lock as described (Slice 3 / new 2b) is exactly the regression that would have caught the original bug — keep it, and make it assert `Path(cli)/X.csv`.read_bytes() == ui-path bytes for every entity, *including* a no-BOM entity and a with-BOM entity (mirror the existing 2026-06-19 BOM regression test's two-entity assertion).

### Sizing / completeness check (per slice)
- **Slice 1 (source-agnostic extraction):** **OK** — single vertical unit, lands complete (UI extraction becomes byte-identical, `_load_uploaded_file` deleted, no half state). Tighten acceptance to the `load_data`↔`load_from_bytes` frame-equality test on a malformed-row fixture (change 5).
- **Slice 2 (shared orchestration, both callers):** **SPLIT NEEDED** → 2a (CLI adopts `run_transform`, snapshot-isolated) + 2b (UI adopts `run_transform` **+** unified load + parity lock, atomic). See change 2. As written it leaves a UI with shared-transform / private-write window.
- **Slice 3 (unified load + parity):** **FOLD into 2b** (change 2) so the UI never sits half-merged; surface the two behavior changes (change 1) as explicit criteria + tests.
- **Slice 4 (shared diff/anomaly):** **OK after narrowing** to anomaly-compute + the dedup'd `ANOMALY_THRESHOLD` only (change 4). Drops the speculative `DiffRow`/`compute_diff`; lands complete with the UI holding only diff *presentation*.

Net: **3 landing slices** — `1` (extraction), `2a` (CLI→`run_transform`, snapshot guard), `2b` (UI→shared engine+load+parity lock), plus the now-small anomaly-unify which can ride in `2b` or stand as a tiny `3`. This matches the yagni-sentinel's "collapse 4→3."

### Harness impact
- **Architecture tree:** the existing `02_Convert.py` entry (line 87) is **already wrong** — it claims the page "runs `run_pipeline()`" when it runs `run_conversion()`. Correct it in this work. Update `extractor.py` (line 17) for `load_from_bytes`, and `pipeline.py` (line 19) for `run_transform` + the unified anomaly compute. **No new file** if change 3 is adopted (no `orchestrator.py` entry needed).
- **DECISIONS.md:** add a landing entry — "two ETL paths unified behind shared extract/`run_transform`/`DataLoader`; UI is now a thin adapter; CLI↔UI byte-parity test locks it; `enabled_entities` now honored in the Convert page; UI extraction inherits encoding-detection + malformed-row repair."
- **No new STANDARD or agent** required — this is consolidation onto existing patterns (Strategy/registry, single-source-of-truth, fail-loud), all already LIVE in scope. The one promotable lesson at Stage 9: *"a parallel reimplementation in the UI layer is a recurring debt source — prefer a shared ETL-layer stage function over a UI copy"* is already implied by the CLAUDE.md layer-isolation rule; only add a STANDARDS line if a second instance recurs (don't pre-promote).

### Resolution _(orchestrator)_
All 7 required changes + both yagni-sentinel trims folded into Approach / Affected files / Decomposition / Spec above: (1) two UI behavior changes are explicit acceptance criteria in Slice 2b; (2) Slice 2 split into 2a (CLI-only) + 2b (UI atomic flip); (3) no `orchestrator.py` — `run_transform` lives in `pipeline.py`; (4) only `compute_anomalies` unified, diff stays per-surface; (5) extractor held to two entrypoints + bytes-core `newline`/decode pinned as the Slice-1 gate; (6) `TransformOutputs` → NamedTuple; (7) parity test asserts on-disk bytes incl. no-BOM + with-BOM entities. Plus the Problem-section school-year correction and the ARCHITECTURE_TREE line-87 fix. **Cross-model note:** the Fable override for `plan-reviewer` was unavailable on this account, so it ran on the default model — **same-model review on this run** (reduces but does not eliminate shared-blind-spot risk); `yagni-sentinel` likewise same-model.

---

### Confirmation re-review (Stage 3, pre-approval)

RUNNING AS: Opus 4.x — **same-model review** (this confirmation pass and the original plan-review both ran on the Opus family; the builder's authoring also Opus-family per line 96). Independence here is of **role + clean context** — a fresh agent re-grounding every claim against the actual source — **not of model**, so shared blind spots are reduced, not eliminated.

**Verdict: PASS** (with two minor, non-blocking spec-precision notes the author may fold in at Spec or defer — neither is a correctness gate).

**All six confirmation checks hold (re-grounded against source):**
1. **Seam soundness — confirmed.** The lifted range is exactly `pipeline.py:186-234` (school-year determine `186-191` + `set_school_year` `192` + `field_orders` init `197` + entity-order/`enabled_entities` filter `199-206` + entity loop `207-234`). Every CLI side-effect sits *outside* it: `sys.exit` at `139-141`/`147-152`, anomaly check `237-238`, `save_all` `242`, dry-run/diff/quality prints `250-263`, run-log `269`. Both callers build a fresh `DataTransformer()` (`pipeline.py:163`, `02_Convert.py:195`), so lifting the stateful school-year-set + loop as one unit is behavior-preserving. Sound.
2. **Bytes-extractor parity — confirmed.** `_detect_delimiter` already reads raw bytes (`extractor.py:91-92`, `open(...,"rb").readline()`), `_detect_encoding` already reads raw bytes (`extractor.py:114`, `read_bytes()`). The sole correctness risk is reproducing `_read_repaired`'s `open(...,encoding=…,errors=…,newline="")` (`extractor.py:237`) feeding `csv.reader` — and the Slice-1 gate (malformed-`Section` fixture round-tripped through *both* `load_data` and `load_from_bytes`, frame-equal) guards exactly that. Accurate.
3. **Sizing / no half-merged state — confirmed.** 2a is pure extract-method on the CLI path (SD74-isolated); 2b flips the UI to shared `run_transform` **and** `DataLoader.save_all` atomically — the shared-transform/private-write window the original split was designed to avoid is genuinely avoided. Each slice lands complete with all gates + SD74 green.
4. **maintainability-structure (weighted) — confirmed, all three YAGNI calls correct.** (a) `run_transform` in `pipeline.py`: two callers, `02_Convert.py:22` already imports `extract_required_files` from it, no import cycle — a new module would be speculative SRP. (b) Unify only `compute_anomalies`: diff is irreducibly per-surface (the UI stringifies `Previous` at `02_Convert.py:125` to dodge the documented Arrow gotcha; the CLI builds `+N`/`-N` lines at `pipeline.py:353-356`) — a shared `DiffRow` would fight both. (c) Two extractor entrypoints. Layer-isolation goal is met: after 2b the UI holds only diff *presentation* + Streamlit rendering, no ETL business logic. Not under- or over-reaching.
5. **Behavior-change honesty (2b) — confirmed.** Both loader-driven changes are explicit, tested acceptance criteria in Slice 2b: fail-loud `ValueError` on a missing field-map column (`loader.py:110-112`) and strict `df[field_order]` dropping extras (`loader.py:115`) that the UI keeps today (`02_Convert.py:226-227`). The plain-English block (line 147) surfaces both to the approver. Good.
6. **Harness impact — confirmed.** The ARCHITECTURE_TREE line-87 entry IS wrong (says the page "runs `run_pipeline()`"; it runs `run_conversion()` — `02_Convert.py:167,323`) and is captured as a fix; `extractor.py` (line 17) + `pipeline.py` (line 19) description updates and the DECISIONS landing entry are listed. No new STANDARD or agent — correct (consolidation onto patterns already LIVE).

**New issues found this pass (both MINOR — spec precision, not blocking):**
- **(N1) Slice 1 introduces a *third*, unnamed UI behavior change: the extractor swap brings `dtype=str` to the Convert page.** Today's `_load_uploaded_file` does **not** pass `dtype=str` (`02_Convert.py:77` kwargs; the last-resort branch at `85-90` likewise omits it), so numeric-looking code columns (school codes, phone numbers) could be coerced to float and gain a spurious `.0`. After Slice 1 the UI inherits `_read_with_fallback`'s `dtype=str` (`extractor.py:164`). This is strictly an *improvement* and is covered indirectly by the Slice-1 frame-equality gate (which compares against `load_data`, the `dtype=str` source of truth) and the parity lock — but for parity with how the plan honestly names the two loader changes in 2b, add a one-line Slice-1 acceptance note: *"UI extraction now reads all columns as `str` (no float-coercion `.0`); covered by the `load_data`↔`load_from_bytes` frame-equality gate."* Not a correctness gate.
- **(N2) Slice-1 spec pins `load_data`'s missing-file semantics but is silent on `load_from_bytes` for a referenced-but-not-uploaded file.** `load_data` inserts an empty DataFrame for a missing *required* file (`extractor.py:45-48`); `load_from_bytes` only ever receives uploaded keys. Behavior is in fact preserved because both the old UI loop (`02_Convert.py:219`) and the lifted `run_transform` loop (`pipeline.py:221`) absorb an absent primary source via `.get(primary_source, pd.DataFrame())` → skip. Worth one explicit spec line so the implementer doesn't try to back-fill empty frames in `load_from_bytes` (it must not — only `.get(...)`-default-skip carries the absence). No code-behavior risk; documentation precision only.

**Sizing / completeness (per slice):** Slice 1 — **OK** (frame-equality gate guards the only real risk). Slice 2a — **OK** (snapshot-isolated extract-method). Slice 2b — **OK** (atomic engine+write flip, parity lock lands with the parity it creates). Slice 3 — **OK** (small, single-sources anomaly compute + the one `ANOMALY_THRESHOLD`; correctly leaves diff per-surface).

**Harness impact:** No new STANDARD or agent. The two minor notes above are Spec-precision edits only; the ARCHITECTURE_TREE/DECISIONS captures already listed are sufficient.

**Confidence:** High on checks 1-6 (every claim re-grounded against `pipeline.py`, `02_Convert.py`, `extractor.py`, `loader.py`, and the existing BOM regression test at `test_loader.py:32-46`). The plan correctly absorbed all 7 prior required changes and both YAGNI trims. The two new findings are genuinely minor — neither blocks approval; fold them into the Spec at the author's discretion.

---

## Spec  _(per slice — Stage 4)_

### Plain-English (read this first)
- **What this builds:** the ad-hoc "Convert" web page and the scheduled/CLI run will share **one** conversion engine instead of two near-copies. Today the web page has its own weaker copy that can silently drop or garble rows and ignores which outputs a district has enabled; after this, both go through the exact same extract → transform → write code.
- **What "done" means for you:** upload the same files on the Convert page or run the scheduled task — you get **byte-for-byte identical** CSVs (a new automated test enforces this). The web page inherits the robust file-reading (handles MyEd's tricky comma-in-Section rows and odd encodings) and honors `enabled_entities`. The StudentAttendance-BOM class of bug can't recur silently.
- **What you're accepting (trade-offs):** two deliberate, *desirable* behavior changes on the Convert page — (1) it now **errors loudly** if a required output column is missing (instead of quietly writing a partial file), and (2) it writes exactly the contract columns (drops any stray extra column). Both match how the scheduled run already behaves. Risk is concentrated in the file-reader refactor and the web page (which has lighter automated coverage) — mitigated by the parity test, the SD74 golden-snapshot, and the Playwright smoke test. No change to output file formats, transformers, or YAML configs.

### In-scope standards dimensions (target bar)
`maintainability-structure` (layer isolation UI↛ETL; SRP; SOLID/DRY) · `data-and-persistence` (encoding/delimiter/repair single-sourced) · `reliability-resilience` (fail-loud on missing columns; repair-pass parity) · `testing` (CLI↔UI byte-parity lock, bytes-extractor tests, 80% gate, SD74 snapshot). Non-negotiables: SOLID > DRY > KISS > YAGNI · fail loudly · single source of truth.

### Slice 1 — Source-agnostic extraction
- **Files & changes:**
  - `src/etl/extractor.py` — extract a private bytes-core from `_read_with_fallback`/`_read_repaired`/`_detect_*` so the parse operates on `(name: str, data: bytes, explicit_names)`. Add `def load_from_bytes(self, sources: dict[str, bytes], file_headers: dict[str,list[str]] | None = None) -> dict[str, pd.DataFrame]`. `load_data` reads each present file's bytes (`Path.read_bytes()`), dispatches to the same core, preserving today's "missing file → empty DataFrame" and "unparseable → `ExtractionError`" semantics. `_read_repaired` bytes-path: `csv.reader(io.StringIO(data.decode(encoding, errors=encoding_errors)), delimiter=sep)` — reproduce the `newline=""`/decode behavior exactly.
  - `src/ui/pages/02_Convert.py` — `run_conversion` reads `uploaded_files` into `{name: buf.getvalue()}` and calls `DataExtractor(...).load_from_bytes(...)`; **delete `_load_uploaded_file`**.
- **Tests to add:** `tests/test_extractor.py` — for each existing disk case add a bytes-path twin; a **malformed-`Section` fixture round-tripped through `load_data` and `load_from_bytes` asserts frame-equal**; encoding-detection on bytes (clean utf-8, utf-8-with-junk → replace, legacy → cp1252/latin1); headerless injection via bytes.
- **Acceptance:** UI uploads parse via the same core (repair + encoding-detect); `_load_uploaded_file` gone; all existing extractor tests + SD74 snapshot green.
  - **N1 (name the third behavior change):** routing the UI through the shared core also brings `DataExtractor`'s `dtype=str` reads to the Convert page (today's `_load_uploaded_file` lets pandas infer dtypes). This is *beneficial* (kills silent numeric coercion of ID-like columns) but is a UI-visible change — state it as an acceptance note; the byte-parity lock (Slice 2b) covers it.
  - **N2 (not-uploaded file semantics):** `load_from_bytes` must preserve `load_data`'s "referenced-but-absent source → skipped, downstream `.get(name, pd.DataFrame())` yields empty frame" behavior — do NOT back-fill empty frames for un-uploaded files. Add a one-line test.

### Slice 2a — `run_transform` in `pipeline.py`, CLI adopts it
- **Files & changes:** `src/etl/pipeline.py` — add `class TransformOutputs(NamedTuple): outputs: dict[str, pd.DataFrame]; field_orders: dict[str, list[str]]` and `def run_transform(raw_data, mappings, global_config) -> TransformOutputs` containing exactly the current `pipeline.py:181-234` body (school-year determine/set, `entity_order` + `enabled_entities` filter, per-entity loop, `field_orders` collection). `run_pipeline` replaces that inline block with `result = run_transform(...)`, then `outputs, field_orders = result`.
- **Tests to add:** `tests/test_pipeline.py` (or `test_main_helpers.py`) — `run_transform` honors `enabled_entities` (excluded entity absent even if its source file present); respects `entity_order`; skips empty-primary; returns correct `field_orders` from `field_map` keys.
- **Acceptance:** CLI behavior unchanged; **SD74 snapshot byte-identical**; UI untouched.

### Slice 2b — UI adopts `run_transform` + unified load + parity lock
- **Files & changes:** `src/ui/pages/02_Convert.py` — `run_conversion` returns `run_transform(raw_data, mappings, global_config)`; the SFTP-upload block writes via `DataLoader(tmpdir).save_all(outputs, field_orders)` (drop the inline `to_csv` temp loop); the download/zip paths keep using `DataLoader.csv_encoding` (today's patch) and `field_orders` for column order. Remove the inline `ordered + extra` field-ordering (now owned by `save_all`).
- **Tests to add:** `tests/test_pipeline_parity.py` *(new)* — same synthetic GDE bytes → (a) `run_pipeline` to a temp dir, (b) UI path (`load_from_bytes`→`run_transform`→`DataLoader.save_all` to a temp dir); assert per-entity **frame-equal AND `read_bytes()`-equal**, covering `StudentAttendance` (no BOM) + a rostering entity (BOM). Fail-loud test: a frame missing a field-map column raises `ValueError` via `save_all`. Column-set parity assertion.
- **Acceptance:** UI outputs byte-identical to CLI; the two behavior changes (fail-loud, extras-dropped) covered by tests; UI smoke test green.

### Slice 3 — Unify the anomaly compute
- **Files & changes:** `src/etl/pipeline.py` — keep the single `ANOMALY_THRESHOLD`; factor the pure compute into `def compute_anomalies(outputs, output_dir) -> list[str]` (returns structured warning strings); `_check_anomalies` becomes a thin logger over it. `src/ui/pages/02_Convert.py` — delete `_check_anomalies_ui` + the duplicated `ANOMALY_THRESHOLD`; import and call `compute_anomalies`, render as `st.warning`. Diff stays per-surface (`_compute_diff` retained).
- **Tests to add:** `tests/test_main_helpers.py` — `compute_anomalies` unit tests (>20% drop detected, missing/unreadable previous handled); UI uses the shared compute (no second threshold literal).
- **Acceptance:** anomaly logic single-sourced; one `ANOMALY_THRESHOLD`; CLI + UI warnings derive from the same function.

### Cross-slice acceptance (Definition of Done)
All gates green each slice: full pytest + **SD74 snapshot** + contract + e2e + 80% coverage; ruff + ruff format; mypy (non-UI); bandit; `scripts/claugentic-check_architecture_tree.py`. ARCHITECTURE_TREE corrected (line 87) + updated; DECISIONS entry on final landing. `/simplify` + `/code-review` at Verify; `architect-reviewer` audit of the in-scope dimensions.
