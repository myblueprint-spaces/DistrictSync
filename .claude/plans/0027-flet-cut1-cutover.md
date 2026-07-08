<!-- claugentic-dev-harness@0.3.0 -->
# 0027 — CUT-1: Cutover — Flet becomes the ONLY UI (remove Streamlit + `src/ui`)

- **Status:** DRAFT → adversarial gate → Spec → build
- **Parent program:** [`0013-flet-production-redesign.md`](0013-flet-production-redesign.md) (CUT-1 row — "5 Cutover") · the FINAL slice · follows all IA-1..IA-9 (Flet UI complete) + PLAT-0..3.
- **References:** `src/main.py:188-210` (the `_select_ui_launcher` seam + no-argv `__main__` dispatch to flip) · `docs/FLET_1.0_CONVENTIONS.md:110,162-163` (the CUT-1 flip contract — only the UI branch changes; core byte-identical) · `.claude/plans/0013-HANDOFF.md:19-22` (the migrate-before-delete gate catch) · `.claude/plans/0015-flet-plat3-release.md:58,62` (the release repackage design — "swap pack target + flip default + drop the Streamlit job") · `docs/claugentic-ROADMAP.md:99` (CUT-1 folder_picker + tkinter drop).

## Problem
The Flet UI (`src/ui_flet/`) is COMPLETE — every nav destination (IA-1..IA-9) has a real surface. Yet `src/main.py`'s no-argv branch still launches **Streamlit** by default (`_select_ui_launcher("")` → `src/ui/launcher.py`); Flet is only reachable via `DISTRICTSYNC_UI=flet`. Streamlit `src/ui/` has been the deliberate **rollback floor** across ~14 slices. CUT-1 is the ONE intended exception to "`main.py` stays unchanged": it **flips the default to Flet**, **deletes `src/ui/`**, and **removes the `streamlit` dependency + its build/CI/release scaffolding** — leaving Flet as the only UI and the public release exe.

This is the highest-coupling slice: a botched cutover leaves a dangling `streamlit` dep, a test importing a deleted module, or a `release.yml` still building a dead Streamlit exe. The safety net is the **migrate-before-delete checklist** (every deleted capability must have a confirmed Flet home + a passing test) — and the inventory below confirms every named migration already landed, so the deletion loses nothing.

## Inventory — every `src/ui` / `streamlit` consumer (grepped 2026-07-04)

**Runtime `src.ui` imports outside `src/ui/` itself:** exactly ONE — `src/main.py:200` (`from src.ui.launcher import main`). Everything else under `src.ui.*` is internal to the `src/ui/` tree (Home/pages/brand/folder_picker/mapping_helpers). **No `src/ui_flet` module and no core module imports `src.ui`.** The tree is a clean island rooted at `main.py:200`.

| # | Consumer | What it does | CUT-1 action |
|---|----------|--------------|--------------|
| 1 | `src/main.py:188-210` | `_select_ui_launcher(ui_mode)` picks Streamlit (default) vs Flet (`=flet`); no-argv `__main__` reads `DISTRICTSYNC_UI` then dispatches | **FLIP** — default → Flet; remove the `src.ui.launcher` import (§Flip) |
| 2 | `src/ui/**` (Home, brand, folder_picker, mapping_helpers, launcher, pages 01-05) | The whole Streamlit app + launcher | **DELETE** (§Delete) |
| 3 | `tests/test_pipeline_parity.py` | **CRITICAL parity lock.** `_run_ui_path` (L267-277) reproduces `load_from_bytes → run_transform → DataLoader.save_all` **INLINE** — it does **NOT** import `src/ui/pages/02_Convert.py` | **NO CHANGE** — parity lock already decoupled from Streamlit; stays green as-is |
| 4 | `tests/test_wizard_schedule_errors.py` | Loads `src/ui/pages/01_Setup_Wizard.py` **by file path** (`importlib`) to test `_classify_schedule_error` | **DELETE** — superseded by `tests/test_ui_flet_setup_errors.py` (tests the migrated `classify_schedule_error`) |
| 5 | `tests/test_folder_picker.py` | `from src.ui.folder_picker import pick_directory` | **DELETE** — folder_picker superseded by `src/ui_flet/filepicker.py` (ROADMAP CUT-1 item) |
| 6 | `tests/test_ui_smoke.py` | Playwright Streamlit smoke (`streamlit_server` fixture, all 5 pages) | **DELETE** — Flet has no browser server to smoke; the exe smoke is `ci_flet_pack_smoke.py` |
| 7 | `tests/conftest.py:24-70` | `streamlit_server` session fixture (spawns `streamlit run src/ui/Home.py`) | **REMOVE** the fixture (+ its imports) |
| 8 | `tests/test_ui_flet_routing.py` | Tests `_select_ui_launcher` — asserts `""`/unknown/`"FLET"` → Streamlit, `"flet"` → Flet | **REWRITE** to the flipped contract (§Flip test) |
| 9 | `tests/test_ui_flet_help.py:29` | **Comment-only** grep-consistency reference to `src/ui/Home.py`/`05_Help.py` (no runtime import; assertions self-contained) | **NO CHANGE** (optionally scrub the stale comment) |
| 10 | `requirements.txt:11-13` | `streamlit>=1.30` runtime dep + comment | **REMOVE** the dep + rewrite the comment |
| 11 | `pyproject.toml` | L34 `[project.optional-dependencies] ui`, L82 `[tool.coverage.run] omit "src/ui/*"`, L72 `ui:` marker, L134-136 `[[tool.mypy.overrides]] src.ui.*` | **REMOVE** all four `src.ui`/streamlit refs |
| 12 | `.github/workflows/ci.yml:33` | `mypy --exclude 'src/ui\|src/ui_flet'` | **DROP** the `src/ui` alternation → `--exclude 'src/ui_flet'` |
| 13 | `.github/workflows/release.yml` | 3 Streamlit console jobs (`build-windows/linux/macos` = `pyinstaller src/main.py --collect-all streamlit --hidden-import src.ui.*`) + `publish-release` ships them | **REPACKAGE** — Streamlit jobs dropped; Flet-default `main.py` becomes the public exe (§Repackage) |
| 14 | `.github/workflows/flet-pack.yml:108,147-148` | Packs `src/ui_flet/launcher.py` as `DistrictSync-flet`; `--exclude-module streamlit`/`src.ui` | **RETARGET** pack `src/main.py` (Flet-default) → the public exe (§Repackage) |
| 15 | `Makefile:22-23,47-80,131-132` | `ui:` target (`streamlit run`), `build-win` (Streamlit exe), `typecheck` exclude | **UPDATE** — drop `ui:` + Streamlit `build-win`; retarget `build-flet-win`; fix `typecheck` |
| 16 | `.streamlit/config.toml` | Streamlit server/theme config | **DELETE** (dead once Streamlit is gone) |
| 17 | `docs/claugentic-ARCHITECTURE_TREE.md` | `## src/ui/` section + 5 page entries + `src/ui/*` file entries; tree entries for deleted tests; `requirements.txt`/`conftest.py`/`test_ui_smoke.py` descriptions mention Streamlit | **UPDATE** — remove deleted-file entries; fix descriptions (tree-check gate) |
| 18 | `CLAUDE.md` (NOISE — pre-existing uncommitted edits) | `streamlit run src/ui/Home.py`, `### Web UI (src/ui/)`, Help "single source shared with MkDocs", Streamlit Arrow gotcha, coverage-omit line, App URL | **DO NOT STAGE** — defer prose cleanup to the user's noise-resolution (§CLAUDE.md tension) |
| 19 | `README.md:88`, `docs/developer/setup.md`, `docs/developer/testing.md:126-127`, `CHANGELOG.md` | Doc prose mentioning `streamlit run` / `src/ui/*` coverage-omit | **UPDATE** (docs/traceability) — README + developer docs are in-scope; CHANGELOG gets a CUT-1 entry |

### Migrate-before-delete confirmation (every named migration ALREADY landed)
| Capability (in `src/ui/`) | Confirmed Flet home | Test proving it | Verdict |
|---|---|---|---|
| `_classify_schedule_error` (Setup Wizard) | `src/ui_flet/setup_errors.py` `classify_schedule_error` (no flet/streamlit import) | `tests/test_ui_flet_setup_errors.py` | ✅ migrated |
| `__DISTRICTSYNC_RUN__` run-history parser | `src/ui_flet/run_log.py` (`TAG`, `read_run_records`) + `run_history.py` | `tests/test_ui_flet_run_log.py`, `test_ui_flet_run_history.py` | ✅ migrated |
| Brand values (`MB_*`, palette) | `src/ui_flet/tokens.py` (8 `MB_*` primitives, verbatim) | `tests/test_ui_flet_tokens.py` (verbatim-port assertion) | ✅ migrated |
| Convert adapter (`run_conversion` → `load_from_bytes`/`run_transform`/`save_all`) | `src/ui_flet/screens/convert.py` `convert_job` + `convert_result.py` | `tests/test_ui_flet_convert_result.py` + **`test_pipeline_parity.py` (inline, not via src/ui)** | ✅ migrated + parity intact |
| Folder picker (`folder_picker.pick_directory`) | `src/ui_flet/filepicker.py` / `picker_field.py` | `tests/test_ui_flet_filepicker.py` | ✅ superseded |
| Mapping Editor (`mapping_helpers`) | `src/ui_flet/mapping_catalog.py` + IA-8 `screens/mapping.py` | `tests/test_ui_flet_mapping_catalog.py` | ✅ migrated |
| Help page (docs render) | `src/ui_flet/screens/help.py` (IA-7) | `tests/test_ui_flet_help.py` | ✅ migrated |

**Conclusion:** `src/ui/` holds NO unique logic the Flet path or any surviving test needs. The deletion is safe.

## Goals / Non-goals
- **Goal — flip the default UI to Flet:** `main.py`'s no-argv branch launches the Flet shell by default; the CLI branch (argv>1) + `sys.exit(0/1/2/3)` stay **byte-identical**.
- **Goal — delete Streamlit entirely:** remove `src/ui/`, the `streamlit` dependency (`requirements.txt` + `pyproject.toml`), `.streamlit/config.toml`, the Streamlit-only tests + `conftest.py` fixture, and every Streamlit build/CI/release/mypy/coverage reference.
- **Goal — repackage the release:** the Flet-default exe (packed from `main.py`) becomes THE public release (1/OS + SHA-256); the 3 Streamlit console jobs are dropped.
- **Goal — no half-cutover / no new tech debt:** no dangling `streamlit` import, no test referencing a deleted module, no dead build flag. Full suite (minus the 3 deleted Streamlit-only test files) green ≥80%; parity lock + SD74 snapshot green.
- **Non-goal — the ETL/CLI core.** `src/etl|config|sftp|scheduler` and `main.py`'s CLI branch are **byte-identical**. ONLY `main.py`'s no-argv UI dispatch flips.
- **Non-goal — new Flet surface content.** IA-1..IA-9 are done; CUT-1 changes only what's the default + what's deleted, not any Flet screen.
- **Non-goal — code-signing / new release features** (PLAT-4). SHA-256 checksums already landed (PLAT-3); CUT-1 only drops the "preview" framing and the Streamlit exe.
- **Non-goal — sweeping `CLAUDE.md`.** Its stale Streamlit prose is legitimately dead post-CUT-1, but it carries unrelated pre-existing uncommitted edits — see §CLAUDE.md tension.

## Approach

### A. The `main.py` flip (exact dispatch change; exit codes preserved)
Today (`src/main.py:188-210`):
```python
def _select_ui_launcher(ui_mode: str):
    if ui_mode == "flet":
        from src.ui_flet.launcher import main as _launch_ui
    else:
        from src.ui.launcher import main as _launch_ui   # ← DELETE this branch
    return _launch_ui

if __name__ == "__main__":
    if len(sys.argv) == 1:
        _ui_mode = os.environ.get("DISTRICTSYNC_UI", "").strip().lower()
        _select_ui_launcher(_ui_mode)()
        sys.exit(0)
    ...  # CLI branch — UNTOUCHED
```
CUT-1 collapses the seam to a single launcher. **DECISION — retire `DISTRICTSYNC_UI` and `_select_ui_launcher`** (there is only one UI left; keeping a dead escape hatch is YAGNI and confuses the contract). The no-argv branch becomes:
```python
if __name__ == "__main__":
    if len(sys.argv) == 1:
        from src.ui_flet.launcher import main as _launch_ui
        _launch_ui()
        sys.exit(0)
    ...  # CLI branch — byte-identical
```
- Delete `_select_ui_launcher` + its `__all__`/comment references + the `import os` use if now unused (keep `os` — used by `_read_sftp_password`).
- **The CLI branch, all flags, the SFTP subcommands, and `sys.exit(0/1/2/3)` are verbatim unchanged** — verified: nothing in the flip touches L212-314.
- `ft.run` blocks until the window closes, so the existing `sys.exit(0)` after the launch still holds (same as the Streamlit launcher's blocking call — confirmed in `src/ui_flet/launcher.py:130-146`).

**Flip test (§8, no window launch):** replace `tests/test_ui_flet_routing.py` with a test asserting the no-argv dispatch resolves to `src.ui_flet.launcher.main` and the CLI path is unaffected. Since `_select_ui_launcher` is removed, the test imports the launcher and asserts the dispatch target by a **seam that doesn't launch** — either (a) keep a tiny `_default_ui_launcher()` helper that returns `src.ui_flet.launcher.main` (one-liner, testable by identity via monkeypatched sentinel — mirrors the old routing test's identity-assertion pattern), or (b) assert via `importlib`/`inspect` that the no-argv block references the flet launcher. **Recommend (a)** — a named `_default_ui_launcher()` keeps the dispatch testable-by-identity without launching a window, at trivial cost, and documents the single remaining entry. (Design question resolved below.)

### B. Delete `src/ui/` + Streamlit-only tests
- `git rm -r src/ui/` (Home, brand, folder_picker, mapping_helpers, launcher, `pages/01-05`).
- `git rm tests/test_wizard_schedule_errors.py tests/test_folder_picker.py tests/test_ui_smoke.py`.
- `.streamlit/config.toml` → `git rm`.
- `tests/conftest.py` — remove the `streamlit_server` fixture (L24-70) + its now-unused `subprocess`/`sys`/`time` imports **only if** no other fixture uses them (verify before pruning imports).
- `tests/test_ui_flet_help.py:29` — optionally scrub the stale `src/ui/Home.py` comment reference (cosmetic; not load-bearing).

### C. Drop the `streamlit` dependency + dead excludes
- `requirements.txt` — remove `streamlit>=1.30` (L13) + rewrite the L11-12 comment (the no-arg exe now opens Flet).
- `pyproject.toml` — remove `streamlit>=1.30` from `[project.optional-dependencies] ui` (or delete the `ui` extra entirely — it existed only for Streamlit; the Flet extra is `flet-ui`); remove `"src/ui/*"` from `[tool.coverage.run] omit`; remove the `ui:` pytest marker (L72) if no test still uses `@pytest.mark.ui`; remove the `[[tool.mypy.overrides]] module = ["src.ui.*"]` block (L134-136).
- `.github/workflows/ci.yml:33` — `mypy src/ --exclude 'src/ui|src/ui_flet'` → `--exclude 'src/ui_flet'`.
- `Makefile` — delete the `ui:` target + the Streamlit `build-win` target; fix `typecheck` exclude; keep/retarget `build-flet-win` (or rename to `build-win` now that it's the only exe — see §D).

### D. CI / packaging repackage (Flet-default exe = the public release)
Per the 0013 row + PLAT-3's design ("swap the pack target + flip default + drop the Streamlit job", `0015:58`):
- **`flet-pack.yml`** — change the pack target from `src/ui_flet/launcher.py` to **`src/main.py`** (now Flet-default) and the `--name` from `DistrictSync-flet` to **`DistrictSync`**. Everything else (per-OS flavor matrix, offline-embed warm-up + asserts, `ci_flet_pack_smoke.py`, `--exclude-module streamlit`/`src.ui` — now belt-and-braces against a stale transitive) stays. The smoke script's `resolve_artifact`/orphan logic keys on the base name → update the name arg passed by the workflow to `DistrictSync`. **Keep `--exclude-module streamlit src.ui`** even though both are gone — cheap insurance that a stray transitive never re-bloats the exe.
- **`release.yml`** — **delete** `build-windows`/`build-linux`/`build-macos` (the 3 Streamlit console jobs). `build-flet` (`uses: ./flet-pack.yml`) becomes THE builder. `publish-release` now `needs: [build-flet]`, downloads the `DistrictSync-<os>` artifacts from the matrix, computes SHA-256, and ships **1 exe/OS** as the public Release. **Drop the "preview / in development / use the stable download" framing** from the Release body — the Flet exe IS the product now. Update the download table Notes (double-click opens the app; no "redesigned app in development" line).
- **`ci_flet_pack_smoke.py`** — no logic change; it's invoked with the new base name (`DistrictSync`) by the retargeted workflow. The `_ETL_LOG` boot-failure path already matches `main.py`'s launcher (both write to `user_log_file()`).
- **`Makefile`** — the local `build-flet-win` retargets to `src/main.py --name DistrictSync` to mirror CI; consider renaming it `build-win` (the only exe). Keep the smoke one-liner comment.

**Verification of the smoke's process model:** `main.py` no-argv → `src.ui_flet.launcher.main()` → `ft.run(shell.main)` — the same `exe → re-exec'd host → flet view` tree the smoke walks (the smoke's `_tree_pids` matches on `districtsync-flet*`; with the rename to `DistrictSync` the match prefix must update to `districtsync*` — **flag for the implementer**: `ci_flet_pack_smoke.py:169` `nm.startswith("districtsync-flet")` needs to become `districtsync` to keep tracking the renamed re-exec'd host). This is the one non-obvious smoke coupling the rename touches.

### E. CLAUDE.md-noise tension (do NOT stage)
`CLAUDE.md` is one of the 8 NOISE files (it has pre-existing uncommitted edits unrelated to CUT-1) and it holds stale Streamlit prose (`streamlit run src/ui/Home.py`, `### Web UI (src/ui/)`, the Help "single source shared with MkDocs" line, the Streamlit Arrow gotcha, the coverage-omit + App URL lines). CUT-1 legitimately makes those dead, **but staging `CLAUDE.md` would sweep the unrelated pre-existing edits into the slice commit.** 
- **Recommendation:** do **NOT** `git add CLAUDE.md` in this slice. Leave a one-line note in the Spec + the retrospect that the Streamlit prose in `CLAUDE.md` is now stale and belongs to the user's separate noise-resolution pass. The plan touches only the non-noise docs (README, developer docs, ARCHITECTURE_TREE, CHANGELOG). Never stage any of the 8 noise files (`.claude/settings.json`, `CLAUDE.md`, `docs/claugentic-ENGINEERING_STANDARDS.md`, `docs/claugentic-WORKFLOW.md`, `docs/claugentic-standards/README.md`, `scripts/claugentic-check_architecture_tree.py`, `.githooks/`, `docs/claugentic-PLAN_TEMPLATE.md`).

## Architecture & holistic fit
- **Codebase fit** — realizes the terminal state the whole 0013 program aimed at: one UI layer (`src/ui_flet/`), one exe, no Streamlit. Tiers stay clean (UI-only deletion; core untouched). The `main.py` flip is the single intended core-adjacent edit, scoped to the no-argv branch.
- **Quality dimensions:** `maintainability-structure` (one UI, dead-scaffolding removed) · `testing` (parity lock preserved + re-pointed flip test; suite green ≥80%) · `observability-ops` / `docs-traceability` (the public Release contract changes — documented in CHANGELOG + Release body) · `security` (fewer deps = smaller surface; `--exclude-module` belt-and-braces retained) · `reliability-resilience` (the deletion is reversible only via the migrate-before-delete net — every deleted capability has a confirmed Flet home + passing test, tabulated above).
- **Future-proofing** — after CUT-1 the release workflow is single-path (one reusable pack + smoke); adding an OS = a matrix row; code-signing (PLAT-4) drops onto the one exe.

## Affected files
**Edit:**
- `src/main.py` — flip the no-argv branch to Flet; remove `_select_ui_launcher` + `src.ui.launcher` import + related `__all__`/comment. **CLI branch byte-identical.**
- `requirements.txt` — remove `streamlit>=1.30` + comment.
- `pyproject.toml` — remove streamlit from `ui` extra (or drop the extra), `src/ui/*` coverage omit, `src.ui.*` mypy override, `ui` marker (if unused).
- `.github/workflows/ci.yml` — mypy exclude `src/ui|src/ui_flet` → `src/ui_flet`.
- `.github/workflows/release.yml` — delete 3 Streamlit jobs; `build-flet` → THE builder; `publish-release` needs `build-flet`; drop preview framing.
- `.github/workflows/flet-pack.yml` — pack `src/main.py` `--name DistrictSync`; keep excludes + smoke.
- `scripts/ci_flet_pack_smoke.py` — update the re-exec host match prefix `districtsync-flet` → `districtsync` (rename coupling).
- `Makefile` — drop `ui:` + Streamlit `build-win`; retarget/rename `build-flet-win`; fix `typecheck`.
- `tests/conftest.py` — remove `streamlit_server` fixture (+ unused imports).
- `tests/test_ui_flet_routing.py` — rewrite to the flipped-default contract (§Flip test).
- `docs/claugentic-ARCHITECTURE_TREE.md` — remove `src/ui/*` + deleted-test entries; fix Streamlit-mentioning descriptions (tree-check gate).
- `README.md`, `docs/developer/setup.md`, `docs/developer/testing.md`, `CHANGELOG.md` — drop `streamlit run`/`src/ui/*`; CHANGELOG CUT-1 entry.

**Delete (stage the removed paths explicitly):**
- `src/ui/` (entire tree: `Home.py`, `brand.py`, `folder_picker.py`, `mapping_helpers.py`, `launcher.py`, `pages/01_Setup_Wizard.py`..`05_Help.py`).
- `.streamlit/config.toml`.
- `tests/test_wizard_schedule_errors.py`, `tests/test_folder_picker.py`, `tests/test_ui_smoke.py`.

**Do NOT touch:** `src/etl|config|sftp|scheduler`, `main.py` CLI branch, `test_pipeline_parity.py`, all `src/ui_flet/**`, and the 8 noise files (esp. `CLAUDE.md`).

## Risks
- **Parity lock lost when Streamlit goes** → **Refuted by inventory:** `test_pipeline_parity.py::_run_ui_path` reproduces the adapter INLINE (`load_from_bytes → run_transform → DataLoader.save_all`), never importing `src/ui/pages/02_Convert.py`. It stays green with zero change. This is the single most important CUT-1 finding.
- **Deleting a capability with no Flet home** → the migrate-before-delete table confirms all 7 named capabilities landed + are tested. Gate at Verify: `grep -rn "src\.ui\b\|streamlit" src/ tests/` must return **zero** hits outside `src/ui_flet` naming coincidences.
- **CI/release breakage (exe won't build/smoke after retarget)** → the `flet-pack.yml` + smoke already build+smoke `launcher.py` today; retargeting to `main.py` (which calls the same launcher) is low-delta. The one coupling is the smoke's re-exec host-name prefix (flag above). Human `workflow_dispatch` on the fork verifies all 3 OS (acceptance gate).
- **Coverage dips below 80% after deleting Streamlit-only tests** → the deleted tests targeted coverage-**omitted** `src/ui/*`, so removing them can only *raise* the ratio; the Flet modules already carry the gate. Verify the number, don't assume.
- **`ui` pytest marker / `subprocess` import left dangling in conftest** → prune only after confirming no surviving test references them.
- **Staging a noise file** → §E: explicit-path staging only; never `git add -A`.

## Test strategy
- **Parity lock (unchanged):** `test_pipeline_parity.py` stays green verbatim — the CLI-vs-UI-adapter byte-parity is preserved because the UI leg was never Streamlit-coupled.
- **Flip test (new/rewritten):** `test_ui_flet_routing.py` → assert the no-argv dispatch target is `src.ui_flet.launcher.main` (by identity via the `_default_ui_launcher()` seam + monkeypatched sentinel — no window launch), and (optionally) that the CLI branch still parses `--sis`/`--input` unaffected.
- **Full suite green ≥80%:** run `pytest tests/ --cov=src --cov-fail-under=80` after deleting the 3 Streamlit-only test files + rewriting routing; SD74 snapshot regression green (core untouched → trivially).
- **Gates:** `ruff check`/`ruff format --check`, `mypy src/ --exclude 'src/ui_flet'` (no `src/ui` left to exclude), `bandit -r src/`, `make validate-config`, tree-check (deleted `src/**/*.py` entries removed).
- **Human-only acceptance gates (like PLAT-3 — the human runs these, not the agent):**
  1. `workflow_dispatch` the retargeted `flet-pack.yml` on the fork across **Win + Linux + macOS** — offline-embed + zero-orphan-close smoke green on all three.
  2. A **real Win11 GUI check**: double-click the packed `DistrictSync.exe` (no args) → the Flet window opens to the state-aware shell; run `DistrictSync.exe --sis myedbc --input … --output …` → the CLI runs + exits 0 (proving the flip didn't disturb the CLI path).
  3. Confirm the public Release draft attaches exactly 1 exe/OS + `SHA256SUMS.txt`, no Streamlit exe, no "preview" framing.

## Decomposition — RECOMMEND THE 2-SLICE SPLIT
This is the program's highest-coupling slice. **Recommend splitting into CUT-1a + CUT-1b**, each landing green independently:

- **CUT-1a — flip + verify (Streamlit stays as a deletable dead floor).** Flip `main.py`'s no-argv branch to Flet; rewrite `test_ui_flet_routing.py` to the flipped contract; add the flip test. Streamlit `src/ui/` + its deps/tests remain present but no longer the default — a still-intact rollback floor. Lands green: parity lock + suite + SD74 all pass; the default UI is Flet. **Value:** the risky *behavior* change (default flip) lands + is validated in isolation, reversible by one-line revert, with Streamlit still on disk as insurance.
- **CUT-1b — delete + de-scaffold + repackage.** Delete `src/ui/` + `.streamlit/` + the 3 Streamlit-only tests + the `conftest` fixture; drop the `streamlit` dep + all mypy/coverage/marker excludes; retarget `flet-pack.yml`/`release.yml`/`Makefile` + the smoke host-name prefix; update ARCHITECTURE_TREE + README + developer docs + CHANGELOG. Lands green: suite ≥80%, `grep streamlit src/ tests/` == 0, the release builds the Flet-default exe. **Value:** the large irreversible deletion + CI repackage is a separate, reviewable diff — a reviewer sees "flip works" (1a) before "delete everything" (1b), and a 1b regression can't mask a 1a behavior bug.

**Justification for the split over one slice:** the two halves have different risk shapes (1a = a behavior flip, testable + revertible; 1b = a bulk deletion + release-contract change) and different review lenses. Keeping Streamlit deletable-but-present through 1a means the flip can be validated (incl. the human GUI check) with the rollback floor still standing — honoring "reversibility: migrate-before-delete is the safety net" literally. Each fits comfortably in one specialist session with no half-done state. **If the gate prefers one slice**, it is still finishable (the inventory shows the coupling is shallow — one runtime import + mechanical de-scaffolding), but the split is the lower-risk sequencing and is recommended.

## Design questions (resolved in-plan; open for the gate)
1. **`DISTRICTSYNC_UI` escape hatch — keep or retire?** **Resolved: retire.** One UI remains; a dead env-var branch is YAGNI and muddies the "Flet is the only UI" contract. Removing it deletes `_select_ui_launcher` too. *(Gate may override if a Streamlit-fallback safety valve is wanted through one release — but that contradicts the deletion in the same slice.)*
2. **Flip-test seam without launching a window.** **Resolved: keep a named `_default_ui_launcher() -> Callable` one-liner** returning `src.ui_flet.launcher.main`, asserted by identity via a monkeypatched sentinel (mirrors the retired routing test's pattern). Trivial cost, keeps the dispatch testable + self-documenting.
3. **Drop the `pyproject` `ui` extra entirely, or just strip streamlit from it?** **Lean: drop the extra** (it existed only for the Streamlit UI; `flet-ui` is the live extra). Gate to confirm no external tooling references `.[ui]`.

## Review

`RUNNING AS: Opus 4.x` — *a separate clean-context plan-gate pass on the most capable model; a reduction of rubber-stamping risk, not an independent-model guarantee. Same model family the plan may have been drafted with, so it does not de-correlate blind spots.*

**Verdict: CHANGES REQUIRED** (the highest-coupling slice — gated hard. The three LOAD-BEARING deletion-safety claims all VERIFY against source: the parity lock is inline-decoupled and stays green with zero change; the only non-island runtime `src.ui` import is `main.py:200`; every migrate-before-delete capability has a confirmed, tested Flet home. The `main.py` flip is correctly scoped to the no-argv branch (exit codes untouched). BUT the CI/release repackage has **two concrete integration breaks** the plan glosses — a real download-artifact-name mismatch between `flet-pack.yml` and `release.yml`, and a set of `--name`-keyed couplings the plan reduces to "one smoke prefix" — plus **two missed live files** (`docs/FLET_1.0_CONVENTIONS.md`, and the `flet-verify.yml` co-caller acknowledgement) that leave a half-cutover doc remnant. Fix these and the slice is clean. No design rejection.)

### VERIFIED against source — every load-bearing deletion-safety claim holds

- **Parity lock is inline-decoupled — TRUE (the single most important CUT-1 claim).** `tests/test_pipeline_parity.py::_run_ui_path` (L267-277) reproduces `load_from_bytes → run_transform → DataLoader.save_all` INLINE, importing only `src.config.loader` + `src.etl.*` — **zero `src.ui` import** anywhere in the file. Deleting `src/ui/` cannot touch it; it stays green verbatim. The CLI↔UI byte-parity lock (incl. the StudentAttendance no-BOM / rostering-BOM split) survives.
- **"Exactly ONE runtime `src.ui` import outside the island" — TRUE.** Independent `grep -rn` across `src/ tests/ scripts/ .github/`: the only non-`src/ui/`-internal, non-test, non-comment runtime import of `src.ui.*` is `src/main.py:200` (`from src.ui.launcher import main`). Every other hit is (a) internal to `src/ui/**`, (b) a test the plan already actions, (c) a string/comment in `src/ui_flet/**` or docs. No `src/ui_flet` module and no core module imports `src.ui`. The island is clean.
- **Migrate-before-delete is COMPLETE + already landed.** IA-9's consolidation has already landed on this branch — `convert_result.py:33`, `home_status.py:39`, `run_history.py:48` already `from src.ui_flet.humanize import …`; `setup.py:71` already imports `friendly_sftp_reason`. Every named capability has a live Flet home with a test: `classify_schedule_error`→`setup_errors.py` (+`test_ui_flet_setup_errors.py`), run-log parser→`run_log.py` (+`test_ui_flet_run_log.py`), brand→`tokens.py` (+`test_ui_flet_tokens.py` verbatim-port), Convert adapter→`convert_job`/`convert_result.py` (+ the inline parity test), folder_picker→`filepicker.py` (+`test_ui_flet_filepicker.py`), mapping→`mapping_catalog.py` (+test), help→`screens/help.py` (+test). `src/ui/` holds no unique logic a survivor needs.
- **The `main.py` flip is correctly scoped — CLI + exit codes byte-identical.** The no-argv block (L204-210) is fully separable from the CLI branch (L212-314); `sys.exit(0/1/2/3)` live entirely in the CLI/SFTP paths the flip never touches. `os` stays needed post-flip (`main.py:77`, `_read_sftp_password`) — the plan already keeps it. `ft.run` blocks (same as the Streamlit launcher), so the trailing `sys.exit(0)` still holds.
- **Retiring `DISTRICTSYNC_UI` is SAFE.** No test, CI job, or `.claude/settings.json` depends on `DISTRICTSYNC_UI=flet`; the only runtime reader is the flipped branch itself (`main.py:208`). The routing test (`test_ui_flet_routing.py`) imports `src.ui.launcher` at 3 sites and IS actioned (REWRITE, row 8).
- **Coverage cannot regress.** The deleted tests targeted coverage-**omitted** `src/ui/*` (`pyproject.toml:82`); removing them can only *raise* the ratio. The `conftest` `subprocess`/`sys`/`time` imports are used ONLY by the `streamlit_server` fixture (teardown L76-79) — safe to prune with the fixture (the plan's "verify before pruning" is right). `flet==0.85.3`/`flet-desktop==0.85.3` correctly SURVIVE in `requirements.txt`.

### Required changes (numbered, actionable — the half-cutover remnants + the CI integration breaks)

1. **`release.yml` `publish-release` download-artifact NAMES do not match `flet-pack.yml`'s upload names — a concrete integration break the plan glosses. [reliability/CI — the release job will fail to find its artifacts as written.]** `publish-release` currently downloads artifacts named `DistrictSync-windows` / `DistrictSync-linux` / `DistrictSync-macos` (the 3 Streamlit jobs' `upload-artifact` names, `release.yml:74/116/157`). But `flet-pack.yml` uploads `DistrictSync-flet-${{ matrix.os }}` (`flet-pack.yml:189`) — i.e. **`DistrictSync-flet-windows-latest`, `DistrictSync-flet-ubuntu-22.04`, `DistrictSync-flet-macos-latest`** (matrix-OS-suffixed, `-latest`/`-22.04`, and still `-flet`). The plan says `publish-release` "downloads the `DistrictSync-<os>` artifacts from the matrix" — that name does not exist. **The Spec must pin BOTH sides of the contract explicitly:** either (a) rename the `flet-pack.yml` `upload-artifact` `name:` to a clean per-OS name (`DistrictSync-${{ matrix.os }}` or a mapped `windows/linux/macos`), OR (b) update `publish-release`'s three `download-artifact` `name:` values to the exact `DistrictSync-flet-<matrix.os>` strings AND update the `mv`/rename step (`release.yml:194-199`) + the `path:` globs accordingly. Name the exact artifact strings on both sides. Since `flet-pack.yml` is ALSO consumed by `flet-verify.yml` (see #4), prefer (a) with a stable clean name so both callers stay legible. This is the one CI break that silently ships nothing.

2. **The pack-rename touches MORE than "one smoke prefix" — enumerate ALL `--name`-keyed couplings, not just `ci_flet_pack_smoke.py:169`. [correctness/CI — the plan reduces a multi-site rename to a single line.]** The plan flags only the `_tree_pids` prefix (`ci_flet_pack_smoke.py:169`). Renaming `--name DistrictSync-flet` → `--name DistrictSync` (and retargeting `launcher.py`→`main.py`) also breaks these, ALL of which the Spec must list as edit sites in `flet-pack.yml`: (a) the pack line itself (`:108`); (b) the `--assert-embed` manifest path `build/DistrictSync-flet/Analysis-00.toc` (`:157`, keyed on `--name` → becomes `build/DistrictSync/Analysis-00.toc`); (c) the size-echo paths (`:168-171`, `dist/DistrictSync-flet*`); (d) the two smoke invocations' base-name arg (`:180`, `:184` — `DistrictSync-flet` → `DistrictSync`); (e) the `upload-artifact` `name:`/`path:` (`:189-190`). AND in `Makefile`: the `build-flet-win` pack `--name` (`:93`) + the smoke one-liner comment (`:91`). The `ci_flet_pack_smoke.py:169` prefix change is correct (packing `main.py --name DistrictSync` → re-exec'd host image `districtsync*`, so `nm.startswith("districtsync")` still tracks it) — but it is one of ~8 rename sites, not the whole job. Note for the implementer: `tests/test_ci_flet_pack_smoke.py`'s `resolve_artifact` assertions pass `"DistrictSync-flet"` as an arbitrary *test fixture* arg to a name-parametrized pure helper — they are NOT coupled to the packed name and stay green (call this out so no one "fixes" them spuriously).

3. **`docs/FLET_1.0_CONVENTIONS.md` carries the now-FALSE pack contract — a missed live file; leaving it stale IS a half-cutover doc remnant. [docs/traceability — the next Flet agent will be actively misled.]** This is an authoritative Flet-conventions doc (not one of the 8 noise files). It asserts, verbatim and "**until CUT-1**": *"The real pack target is the launcher, not `main.py` (PLAT-3, until CUT-1): `flet pack src/ui_flet/launcher.py --name DistrictSync-flet`"* (`:102`); *"the Flet exe is a CI-artifact-only preview … NOT attached to the public Release (which stays one Streamlit exe per OS + SHA-256 + a one-line preview note); the dual-mode `main.py` repackage + Streamlit drop is CUT-1"* (`:154`); *"the Streamlit `src/ui/` stays intact as the rollback floor until CUT-1"* (`:163`); and the reference-YAML block (`:134,139`) + `:101,106,107` all name `DistrictSync-flet`/`launcher.py`. **CUT-1 IS the "until CUT-1" moment** — the Affected-files list must ADD `docs/FLET_1.0_CONVENTIONS.md` and update these lines to the post-cutover truth (pack `main.py --name DistrictSync`; the Flet exe IS the public Release; `src/ui/` is gone). Otherwise a live conventions doc contradicts the workflow it documents.

4. **Acknowledge `flet-verify.yml` as the SECOND consumer of `flet-pack.yml` — and the fork-dispatch entry point. [CI honesty + human-gate correctness.]** `flet-pack.yml` is `on: workflow_call` and is called by BOTH `release.yml` (tag) AND `.github/workflows/flet-verify.yml` (PR + `workflow_dispatch` — confirmed by the glob + `FLET_1.0_CONVENTIONS.md:154`). The retarget in #2 flows into `flet-verify.yml` automatically (shared file — good, no separate edit), BUT the plan never mentions `flet-verify.yml`, and the human-only acceptance gate ("`workflow_dispatch` the retargeted `flet-pack.yml` on the fork across Win+Linux+macOS") is run THROUGH `flet-verify.yml`'s `workflow_dispatch`, not `flet-pack.yml` directly (a `workflow_call`-only workflow has no dispatch trigger). The Spec must name `flet-verify.yml` as the dispatch entry the human uses, and confirm the retarget leaves its non-release path green.

5. **Purge the `ui` pytest marker from `addopts` too, not just the marker definition. [no half-cutover — a dead marker reference in the deselect expression.]** The plan removes the `ui:` marker (`pyproject.toml:72`) "if unused". But the marker is ALSO named in `addopts` at `pyproject.toml:67`: `-m 'not benchmark and not ui'`. Removing the definition without fixing `addopts` leaves `not ui` referencing an undefined marker — harmless today (the expression still evaluates) but exactly the kind of dead reference a "no half-cutover" bar forbids. The Spec must update `addopts` → `-m 'not benchmark'` in the same edit. (The `ui` marker's only user is the deleted `test_ui_smoke.py`, so the definition IS removable.)

6. **Guard-rail: the `DISTRICTSYNC_UI` retirement must NOT sweep the DISTINCT `DISTRICTSYNC_UI_DEMO` flag. [correctness — prefix collision.]** `DISTRICTSYNC_UI_DEMO` is a SEPARATE, surviving dev-only flag (`shell.py:202`, `test_ui_flet_help.py:63/70/77` — routes Help to the design-system gallery). It shares the `DISTRICTSYNC_UI` prefix. The Spec's flip section must state that only the exact `DISTRICTSYNC_UI` var + `_select_ui_launcher` + their docstring/comment references (`main.py:191,206`) are removed, and `DISTRICTSYNC_UI_DEMO` and its shell wiring are UNTOUCHED. A grep-by-exact-name (not prefix) at Verify.

### Sizing / completeness check — RULING: SPLIT INTO CUT-1a + CUT-1b (as the plan recommends) — CONFIRMED, with tightened landing definitions

The plan's 2-slice recommendation is **correct and I am ruling for it** (not merely permitting it). The two halves have genuinely different risk shapes and review lenses, and — decisively — the split keeps a **validated rollback floor standing through the behavior flip**, which is the literal meaning of "migrate-before-delete is the safety net" for the program's single highest-coupling change. One slice is *technically* finishable (the coupling is shallow — one runtime import + mechanical de-scaffolding), but it would conflate an irreversible bulk deletion + a public-release-contract change with a one-line behavior flip in a single un-bisectable diff. Reject the one-slice option for THIS slice.

- **CUT-1a — flip + verify (Streamlit stays as a deletable dead floor). Lands COMPLETE when:** `main.py`'s no-argv branch launches Flet by default; `_select_ui_launcher` + the `src.ui.launcher` import removed; `DISTRICTSYNC_UI` retired (per #6); `test_ui_flet_routing.py` rewritten to the flipped contract via the `_default_ui_launcher()` seam (asserted by monkeypatched-sentinel identity, no window launch — the plan's Design-Q 2 seam is the right, testable choice — keep it); parity lock + SD74 snapshot + full suite green ≥80%. Streamlit `src/ui/` + its deps/tests/CI remain on disk but off the default path — a validated, one-line-revertible rollback floor. **No half-state:** Flet is the sole default AND still fully reversible. This is the risky behavior change in isolation.
- **CUT-1b — delete + de-scaffold + repackage. Lands COMPLETE when:** `src/ui/` + `.streamlit/config.toml` + the 3 Streamlit-only tests + the `conftest` fixture deleted; `streamlit` dep dropped from `requirements.txt` + `pyproject.toml` (`ui` extra + coverage-omit + mypy override + the `addopts`/marker per #5); `ci.yml` mypy exclude narrowed; `release.yml`/`flet-pack.yml`/`Makefile` repackaged with the artifact-name contract fixed (#1) + ALL rename sites (#2); `FLET_1.0_CONVENTIONS.md` (#3) + ARCHITECTURE_TREE + README + developer docs + CHANGELOG updated; `flet-verify.yml` acknowledged (#4). **Gate:** `grep -rn "streamlit\|src\.ui\b" src/ tests/ scripts/ .github/` returns ZERO hits (outside `src/ui_flet` naming coincidences + the intentional `--exclude-module` belt-and-braces); suite ≥80%; tree-check green. The irreversible deletion + release-contract change as one reviewable diff, AFTER 1a proved the flip works.

Both fit one ≤1M-context session. The 6 required changes land in CUT-1b (except #6, which lands in CUT-1a with the flip) — they make CUT-1b *complete*, not larger.

### KEPT spine (sound — do not churn)
- **The 2-slice split: CORRECT and RULED FOR.** Different risk shapes; keeps a validated rollback floor standing through the flip. Keep.
- **Retire `DISTRICTSYNC_UI` + `_select_ui_launcher` (Design-Q 1): CORRECT.** One UI remains; a dead env-var escape hatch is YAGNI and muddies the "Flet is the only UI" contract. Nothing depends on the var (verified). Keep — with the #6 prefix guard-rail.
- **The `_default_ui_launcher()` testable seam (Design-Q 2): CORRECT.** A named one-liner returning `src.ui_flet.launcher.main`, asserted by monkeypatched-sentinel identity, keeps the dispatch testable without launching a window — mirrors the retired routing test's pattern. Keep.
- **Parity lock left UNCHANGED: CORRECT.** Verified inline-decoupled; re-pointing it would be churn (and risk). Keep the no-change call — it is the plan's strongest finding.
- **`--exclude-module streamlit src.ui` retained belt-and-braces: CORRECT.** Cheap insurance a stray transitive never re-bloats the exe after the dep is gone. Keep (it will be the only surviving `src.ui` string, by design — the grep-zero gate must allow it).
- **CLAUDE.md NOT staged (§E): CORRECT.** It is a noise file with pre-existing uncommitted edits; its Streamlit prose is legitimately dead post-CUT-1 but belongs to the user's separate noise-resolution pass. Explicit-path staging only; never `git add -A`; never any of the 8 noise files. Keep — leave the one-line "stale, deferred" note in the retrospect.
- **Drop the `pyproject` `ui` extra entirely (Design-Q 3): CORRECT** (it existed only for Streamlit; `flet-ui` is the live extra). Confirm no external tooling references `.[ui]` — none in-repo.
- **Human-only acceptance gates flagged correctly.** The 3-OS fork `workflow_dispatch` + the real-Win11 double-click/CLI check + the Release-draft audit are correctly the human's (the agent cannot push to the fork). Keep — routed through `flet-verify.yml` per #4.

### Harness impact
- **`docs/FLET_1.0_CONVENTIONS.md` must be updated in this slice (#3)** — a living Flet-conventions doc whose "until CUT-1" pack contract this slice invalidates; not a new STANDARD, but a mandated doc-truth update (Stage 9 discipline: leaving it stale would mislead every future Flet agent).
- **The public-Release contract change is a real, documented shift** (Flet-default exe/OS + SHA-256, no Streamlit exe, drop the "preview/in development" framing) — belongs in CHANGELOG + the `release.yml` body, as the plan states. No new agent or WORKFLOW change implied.
- After CUT-1, the release path is single-source (one reusable `flet-pack.yml` + smoke, two callers) — a genuine maintainability win the plan correctly claims.

## Spec

> Folds the plan-gate ruling (see `## Review`). **SPLIT: CUT-1a (flip + verify) then CUT-1b (delete + de-scaffold + repackage)** — ruled for, not merely permitted. The three deletion-safety claims VERIFY; the 6 required changes are folded (mostly into CUT-1b). The load-bearing corrections: the `release.yml`↔`flet-pack.yml` artifact-name mismatch (#1) and the multi-site `--name` rename (#2) are real CI breaks; `docs/FLET_1.0_CONVENTIONS.md` (#3) is a missed live file.

### CUT-1a — flip the default UI to Flet (Streamlit stays as a deletable dead floor)
1. **`src/main.py` — flip the no-argv branch to Flet.** Replace `_select_ui_launcher` + the two-branch import with a single `_default_ui_launcher() -> Callable` one-liner returning `src.ui_flet.launcher.main`, called from the no-argv block; `sys.exit(0)` after it. Remove `DISTRICTSYNC_UI` + `_select_ui_launcher` + their docstring/comment references (`main.py:191,206`). **[gate #6] Do NOT touch `DISTRICTSYNC_UI_DEMO`** (a distinct surviving dev flag). The CLI branch (L212-314), all flags, the SFTP subcommands, and `sys.exit(0/1/2/3)` are **byte-identical**. Keep `import os` (used by `_read_sftp_password`, `main.py:77`).
2. **`tests/test_ui_flet_routing.py` — rewrite to the flipped contract.** Assert the no-argv dispatch target is `src.ui_flet.launcher.main` by identity via the `_default_ui_launcher()` seam + a monkeypatched sentinel (no window launch); drop the 3 `import src.ui.launcher` sites. Optionally assert the CLI branch still parses `--sis`/`--input`.
3. **Gates:** parity lock + SD74 snapshot + full suite green ≥80%; ruff/format; `mypy src/ --exclude 'src/ui|src/ui_flet'` (Streamlit still present in 1a → keep the `src/ui` alternation here; it narrows in 1b); bandit; validate-config; tree-check. **Lands complete:** Flet is the sole default, Streamlit present-but-unreferenced-at-runtime as a one-line-revertible rollback floor.

### CUT-1b — delete Streamlit + de-scaffold + repackage the release
4. **Delete (stage removed paths explicitly):** `git rm -r src/ui/`; `git rm .streamlit/config.toml`; `git rm tests/test_wizard_schedule_errors.py tests/test_folder_picker.py tests/test_ui_smoke.py`.
5. **`tests/conftest.py`** — remove the `streamlit_server` fixture (L24-79) + its now-unused `subprocess`/`sys`/`time` imports (verified used only there).
6. **`requirements.txt`** — remove `streamlit>=1.30` (L13) + rewrite the L11-12/L15 comments (no-arg exe opens Flet; drop "default until CUT-1"). Keep `flet==0.85.3`/`flet-desktop==0.85.3`.
7. **`pyproject.toml`** — drop the `ui` extra (Streamlit-only); remove `"src/ui/*"` from coverage omit (L82); remove the `[[tool.mypy.overrides]] module=["src.ui.*"]` block (L134-136); remove the `ui:` marker (L72) **AND [gate #5] update `addopts` (L67) `-m 'not benchmark and not ui'` → `-m 'not benchmark'`**.
8. **`.github/workflows/ci.yml:33`** — `mypy … --exclude 'src/ui|src/ui_flet'` → `--exclude 'src/ui_flet'`.
9. **`.github/workflows/flet-pack.yml` — retarget + rename [gate #2, ALL sites]:** pack `src/main.py` `--name DistrictSync` (`:108`); update `--assert-embed` manifest path → `build/DistrictSync/Analysis-00.toc` (`:157`, `:160`); size-echo paths → `dist/DistrictSync*` (`:168-171`); both smoke base-name args → `DistrictSync` (`:180,184`); **[gate #1] `upload-artifact` `name:` → a clean stable per-OS name** (e.g. `DistrictSync-${{ matrix.os }}`) `path: dist/DistrictSync*` (`:189-190`). Keep `--exclude-module streamlit src.ui` (belt-and-braces).
10. **`scripts/ci_flet_pack_smoke.py:169`** — `nm.startswith("districtsync-flet")` → `nm.startswith("districtsync")` (tracks the re-exec'd `main.py --name DistrictSync` host). No other logic change. (`tests/test_ci_flet_pack_smoke.py` `resolve_artifact` assertions use `"DistrictSync-flet"` as an arbitrary name-arg fixture → stay green; do not touch.)
11. **`.github/workflows/release.yml` — repackage [gate #1]:** delete `build-windows`/`build-linux`/`build-macos` (the 3 Streamlit console jobs); `build-flet` (`uses: ./flet-pack.yml`) becomes THE builder; `publish-release` `needs: [build-flet]` and its three `download-artifact` `name:` values + the `mv`/rename step (`:194-199`) + `path:` globs match the exact `flet-pack.yml` upload names chosen in #9 (pin both sides). Ship 1 exe/OS + `SHA256SUMS.txt` as the public Release. Drop the "preview / in development / use the stable download" framing (`:222`) and the "web UI Help & Docs" line; the Flet exe IS the product.
12. **`Makefile`** — delete the `ui:` target (`:22-23`) + the Streamlit `build-win` (`:47-80`); retarget `build-flet-win` to `src/main.py --name DistrictSync` (`:93`, rename to `build-win` — the only exe) + fix the smoke comment (`:91`); fix `typecheck` exclude (`:20`) → `'src/ui_flet'`.
13. **`docs/FLET_1.0_CONVENTIONS.md` — [gate #3] update the now-false pack contract:** `:101-102` (pack `main.py --name DistrictSync`, not `launcher.py`/`DistrictSync-flet`), `:106,107` (manifest path/name), `:134,139` (reference YAML), `:154` (the Flet exe IS the public Release; `src/ui/` gone), `:162-163` (`src/ui/` no longer the rollback floor). Remove every "until CUT-1" hedge that CUT-1 resolves.
14. **`docs/claugentic-ARCHITECTURE_TREE.md`** — remove the `## src/ui/` + `### src/ui/pages/` sections (`:77-91`), the deleted-test entries (`test_ui_smoke.py` `:200`, `test_folder_picker.py`, `test_wizard_schedule_errors.py`), and fix Streamlit-mentioning descriptions (`requirements.txt`, `conftest.py`, `setup.md` `:213`). Tree-check `--staged` must pass.
15. **`README.md`, `docs/developer/setup.md`, `docs/developer/testing.md`, `CHANGELOG.md`** — drop `streamlit run`/`src/ui/*` prose; add a CUT-1 CHANGELOG entry (Flet is the only UI; public exe = Flet-default; Streamlit removed).
16. **[gate #4]** Acknowledge `flet-verify.yml` as the second `flet-pack.yml` caller + the fork `workflow_dispatch` entry point; the #9 retarget flows into it (shared file) — confirm its non-release path stays green. No separate edit needed.

### Human-only acceptance gates (the human runs these — the agent cannot push to the fork)
1. `workflow_dispatch` the retargeted pack (via **`flet-verify.yml`**) on the fork across Win + Linux + macOS — offline-embed + zero-orphan-close smoke green on all three, against the `DistrictSync`-named artifact.
2. Real Win11 check: double-click the packed `DistrictSync.exe` (no args) → the Flet shell opens; `DistrictSync.exe --sis myedbc --input … --output …` → the CLI runs + exits 0 (proving the flip left the CLI path byte-identical).
3. Confirm the public Release draft attaches exactly 1 exe/OS + `SHA256SUMS.txt`, no Streamlit exe, no "preview" framing.

### Build discipline (implementer)
- CUT-1a and CUT-1b are **separate branches/commits** off the flet-rebuild base. Commit-early. Stage ONLY the paths each slice names; never `git add -A`; **never any of the 8 noise files (esp. `CLAUDE.md`** — its Streamlit prose is legitimately dead but deferred to the user's noise-resolution). **Zero change to `src/etl|config|sftp|scheduler`** and to `main.py`'s CLI branch. **Verify grep (CUT-1b):** `grep -rn "streamlit\|src\.ui\b" src/ tests/ scripts/ .github/` returns ZERO outside `src/ui_flet` naming coincidences + the intentional `--exclude-module` belt-and-braces. Gates: pytest(80%)/ruff/format/mypy(non-UI)/bandit/validate-config/tree-check + SD74 snapshot.
