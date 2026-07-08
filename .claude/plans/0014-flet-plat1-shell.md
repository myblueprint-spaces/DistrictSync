<!-- claugentic-dev-harness@0.3.0 -->
# 0014 — PLAT-1: productionized Flet shell + lifecycle + worker/picker infra

- **Status:** Spec'd (plan-gate CHANGES REQUIRED applied) → awaiting user approval of the Spec, then build
- **Resumable from:** awaiting user approval of the single-slice Spec below
- **Blockers:** none (PLAT-0 + PLAT-0b cleared — pin/packaging/lifecycle/cross-platform all proven)
- **Flags:** none yet
- **Parent program:** [`0013-flet-production-redesign.md`](0013-flet-production-redesign.md) (PLAT-1 row of the slice table)
- **References:** [`docs/FLET_1.0_CONVENTIONS.md`](../../docs/FLET_1.0_CONVENTIONS.md) (authoritative — READ FIRST) · [`docs/claugentic-DECISIONS.md`](../../docs/claugentic-DECISIONS.md) (2026-06-29 PLAT-0/0b) · `docs/claugentic-ARCHITECTURE_TREE.md` · prototype `docs/reference/flet-prototype-spike/`

## Problem
DistrictSync's UI is being rebuilt as a native Flet 1.0 desktop app (Plan 0013, tech LOCKED). PLAT-0/0b proved the foundations empirically (pin, windowed/no-console/offline packaging on Win+macOS+Linux, zero-orphan close, CI pre-seed). What's missing is the **productionized shell + the reusable infrastructure** every later surface (IA-1..IA-9) will sit on: the app entry that the dual-mode launcher calls, a themed window, the state-aware navigation skeleton, the worker-thread→UI marshalling helper (the #1 Flet 1.0 correctness trap), the `ft.FilePicker` wrapper, an early-failure path so the no-console build can't die silently, an `app_version()` helper, exact dependency pins + a CI assertion, and a windowed-exe smoke. No real surfaces yet (those are IA-1+).

## Goals / Non-goals
- **Goal:** a runnable native Flet window — branded (`ft.Theme` from `brand.py` values), a **flat** `NavigationRail` (grouped state-aware *prominence* deferred to IA-1 — nothing to be prominent about yet), **calm, branded, in-voice placeholder** screens (not "coming soon"/TODO) — reachable **opt-in** from `src/main.py`'s no-argv branch, closing with zero orphans, packaged as a windowed no-console exe.
- **Goal:** the **trust-critical pure logic extracted into coverage-counted modules** — `app_version()`, brand tokens (+ contrast guarantee), theme mapping, nav-state model. (The worker-marshalling + FilePicker *contracts* are captured in `FLET_1.0_CONVENTIONS.md`, not built as code this slice — see Approach.)
- **Goal:** exact-pin the Flet stack + a **gate-enforced CI assertion** the pins match `FLET_1.0_CONVENTIONS.md`.
- **Goal:** harvest the exact 1.0 **API forms** discovered during build into `FLET_1.0_CONVENTIONS.md` (worker marshalling snippet, `SnackBar`/dialog show API, `page.window.*` async forms) — finishing the doc's *pending PLAT-1* sections.
- **Non-goal:** any real surface content — Home dashboard, Setup Wizard, Convert, Run History, Mapping, Help (IA-1..IA-8a) — placeholders only.
- **Non-goal:** making Flet the **default** UI (stays opt-in until CUT-1) — see Approach.
- **Non-goal:** the `ft.FilePicker` wrapper — PLAT-1 ships **no** picker (no file-picking surface exists yet); its first real consumer is IA-5 and the everywhere-migration is PLAT-2. The picker's async-service *contract* is captured in the conventions doc.
- **Non-goal:** the worker-thread `JobRunner` *code* — deferred to IA-5 (its first real `run_pipeline` caller); only the marshalling *contract* is captured in the conventions doc this slice.
- **Non-goal:** touching the ETL/CLI core, `run_pipeline`, CLI flags, or exit codes 0/1/2/3 (reuse UNCHANGED).

## Approach
New additive package `src/ui_flet/` (Streamlit `src/ui/` untouched — rollback floor until CUT-1). The dual-mode entry changes in **one place only** — `src/main.py:188-194`'s no-argv branch — to route to the Flet launcher **when `DISTRICTSYNC_UI=flet`**, else the existing Streamlit launcher (default).

**Why opt-in via env var (the load-bearing design call):** (1) preserves the "Streamlit is the rollback floor / no half-done state" invariant — users aren't stranded on a placeholder shell mid-program; the default flips to Flet at **CUT-1** once surfaces land. (2) The dual-mode contract is `len(sys.argv)==1 → UI`; a *CLI flag* can't select the UI (any argv flips to CLI mode), so an **env var read inside the no-argv branch** is the only clean switch. Rejected: a flag (breaks dual-mode), a config-file toggle (heavier; env var is the minimal reversible switch), making Flet default now (regresses the live UI to placeholders). **One-site read (F3):** `DISTRICTSYNC_UI` is read at exactly ONE place — `main.py`'s no-argv branch — never re-read elsewhere (the launcher is unconditionally Flet once entered). **Who exercises it (P1):** `DISTRICTSYNC_UI=flet` is the documented **dev default for every subsequent UI slice (IA-1..CUT-1)**, so the Flet shell can't silently rot across ~14 slices while Streamlit stays the user-facing default; the dual-mode test asserts routing both ways.

**Worker→UI marshalling (contract only this slice):** the `JobRunner` *code* is deferred to IA-5 (its first real `run_pipeline` caller) per the program's "promote on 2nd use" precedent. PLAT-1 instead **captures the contract in `FLET_1.0_CONVENTIONS.md`**: the exact 1.0 loop-safe worker→UI update mechanism (confirmed against the installed `flet==0.85.3` package — never mutate controls cross-thread), **plus** the failure contract — `run_pipeline` calls `sys.exit(1)` on bad input (`pipeline.py:294/302/305`), and a caught `SystemExit` re-raises at `pipeline.py:421` **before** `_emit_run_log("failed")`, so **no `__DISTRICTSYNC_RUN__` record is written** on that path (a caught `Exception` at 422-425 *does* write one). The eventual `on_error` is therefore the UI's *only* failure signal on the `SystemExit` path — the contract documents that the UI must not assume a run-log record exists on every failure.

**Launcher / early-failure:** `launcher.py` replicates the Streamlit launcher's frozen-cwd handling (`os.chdir(sys._MEIPASS)` when `sys.frozen`, so `config/` resolves for a later `run_pipeline`), then `ft.run(main)`. Around the import+mount it wraps a try/except that writes a full traceback to the log file (`~/.districtsync/etl_tool.log`, same sink as the ETL) **and** shows a minimal error window/dialog before exiting non-zero — so the no-console build can't die silently (the PLAT-0 risk). The pure parts (error-message formatting, log-path resolution, frozen-path resolution) are factored out and tested; the `ft.run` glue is thin/omitted from coverage. **Paints-before-content (P5):** the themed window chrome (brand background + rail frame) paints **before** content mounts (no flash of unstyled window); the early-failure dialog shows **plain language** (the full traceback goes to the log only, never the dialog).

## Architecture & holistic fit
- **Codebase fit** — strict layering: `src/ui_flet/` is presentation-only and calls the core through its existing public entry (`run_pipeline`) — no ETL/config logic leaks into the UI. Mirrors the existing `src/ui/` placement; pure logic lives in importable modules (testable, no Flet/Streamlit coupling), view glue stays thin. `app_version()` goes in **`src/utils/version.py`** as the single source of truth (the existing inline `importlib.metadata.version("districtsync")` in `main.py:196-199` is the same logic — PLAT-1 adds the helper and uses it from the UI; a follow-up may DRY `main.py` onto it, out of scope here to keep the CLI branch untouched).
- **Product fit** — the cockpit a non-technical admin opens 2–3×/yr; PLAT-1 delivers the trustworthy *frame* (brand, calm navigation, never-crashes shell, clean close) so later surfaces drop in. Verdict-first content is IA-1+.
- **Quality dimensions to uphold** (→ real `docs/claugentic-standards/` modules):
  - `maintainability-structure` — layered presentation, pure-core/thin-view split, a plain `dict[destination → placeholder factory]` (a real registry is IA-1's call if ever needed).
  - `reliability-resilience` — early-failure path (no silent death; paints-before-content); zero-orphan close with a documented leave-point seam. (The `JobRunner` resilience contract is captured in the conventions doc; the code lands at IA-5.)
  - `testing` — pure trust-critical logic coverage-counted (80% gate holds); view glue omitted; windowed-exe smoke.
  - `security` — no new secret surface; `FilePicker` returns server-side paths (district server), no path injected into the core unvalidated; env-var switch is read-only.
  - `product-ux` — accessibility as a token/component guarantee (contrast ≥4.5:1 checked at authoring time in tokens test; visible focus; verdict-never-color-alone deferred to DS-1 where verdict appears).
- **Future-proofing** — a plain destination→placeholder `dict` (IA-1.. add a screen by adding an entry); the worker/FilePicker *contracts* live in the conventions doc, built at first real use (IA-5); theme tokens tiered (primitive→semantic) so DS-1 extends without churn. **Boundary to watch (F6):** `shell.py` carries window + rail + placeholder-host + close-lifecycle — acceptable at PLAT-1 size, but split before IA surfaces grow it (IA-1 is the natural point). **YAGNI guard:** placeholders are branded one-liners, not pre-built surfaces; no speculative infra — the worker/picker/registry are deferred to their first real consumers.

## Affected files
- `src/main.py` — **only** the no-argv branch (188-194): read `DISTRICTSYNC_UI`; if `flet` → `src/ui_flet/launcher.py:main()`, else existing Streamlit path. CLI branch + exit codes untouched.
- `src/utils/version.py` — **new**: `app_version() -> str` (wraps `importlib.metadata`, "dev" fallback).
- `src/ui_flet/__init__.py` — **new** package marker.
- `src/ui_flet/tokens.py` — **new**: brand primitive values (ported `MB_*` hex) + semantic aliases + contrast-checked pairs. Pure, covered.
- `src/ui_flet/theme.py` — **new**: `build_theme() -> ft.Theme` (M3 ColorScheme mapping, light-only). Covered.
- `src/ui_flet/nav.py` — **new**: pure nav-state model (destination list + grouping metadata; the `AppConfig`-state→prominence is *modeled* + tested, but the rail renders **flat** this slice — prominence wiring is IA-1). Covered.
- `src/ui_flet/shell.py` — **new**: themed window + **flat** `NavigationRail` widget + plain `dict` placeholder host + close lifecycle with a **documented leave-point seam (no guard logic)** (view; omitted). _(`worker.py` + `filepicker.py` are NOT built this slice — deferred to IA-5/PLAT-2; their contracts go in the conventions doc.)_
- `src/ui_flet/launcher.py` — **new**: `main()` — frozen-cwd, early-failure path, `ft.run`. Pure helpers covered; `ft.run` glue omitted.
- `requirements.txt` — add `flet==0.85.3`, `flet-desktop==0.85.3` (runtime, native).
- `requirements-dev.txt` — add `flet-web==0.85.3`, `flet-cli==0.85.3` (dev/CI/build only — keeps fastapi/uvicorn out of the shipped exe).
- `pyproject.toml` — new `flet-ui` optional group; add `src.ui_flet.*` to mypy ignore-overrides; coverage-omit **only** the view modules (`shell.py`, `launcher.py` glue), NOT the pure ones (`tokens.py`/`theme.py`/`nav.py`).
- `.github/workflows/ci.yml` — (no new job) the pin assertion runs as a pytest test in the existing gate.
- `tests/test_ui_flet_*.py` — new: version, tokens (+contrast), theme, nav model, flet-pin assertion. (No worker tests — deferred to IA-5.)
- `docs/FLET_1.0_CONVENTIONS.md` — fill the *pending PLAT-1* API-form sections from build findings, **including** the worker→UI marshal contract (+ the `SystemExit`/`Exception` `on_error` asymmetry) and the FilePicker async-service contract (the cut code's knowledge, preserved as contracts).
- `docs/claugentic-ARCHITECTURE_TREE.md` — add the `src/ui_flet/` section + `src/utils/version.py` (same change that adds the files — tree gate).
- `docs/claugentic-ROADMAP.md` — add a backlog line (F2): DRY `main.py:196-199` onto `src/utils/version.py:app_version()` (deferred from PLAT-1 to keep the CLI branch untouched).

## Research / grounding
- **Files reviewed (recon):** `src/main.py:188-199` (dual-mode + version), `src/ui/launcher.py:20-59` (frozen-cwd + synthetic-argv launch), `src/ui/brand.py:48-55` (`MB_*` values; CSS = 412 lines, do not port), `src/config/app_config.py:23-89` (`AppConfig`, `is_complete()`, `schedule_registered`), `src/etl/pipeline.py:269-277` (`run_pipeline` sig + `PipelineResult` + internal `sys.exit`), `requirements*.txt`/`pyproject.toml` (pin sites), `.github/workflows/ci.yml` (gates), tree format.
- **Harness docs consulted:** `FLET_1.0_CONVENTIONS.md` (authoritative), `claugentic-DECISIONS.md` (PLAT-0/0b), `claugentic-standards/` modules listed above.
- **Findings:** reuse `MB_*` values + `importlib.metadata` version + `run_pipeline`/`PipelineResult` as-is. Gaps to build: the `src/ui_flet/` package + pins + CI assertion. Gotchas: `run_pipeline` internal `sys.exit` + the `SystemExit`-no-failed-runlog asymmetry (documented as a contract for IA-5's `JobRunner`); frozen-cwd must be replicated or `config/` won't resolve; the exact 1.0 worker-marshal + `SnackBar`/dialog + `page.window.*` async forms must be confirmed against the installed package (don't trust 0.2x memory).

## Risks & mitigations
- **Dual-mode regression** (break the CLI / no-argv path) → change is one localized `if`; the env-var default is Streamlit (unchanged behavior when unset); a test asserts `DISTRICTSYNC_UI` unset → Streamlit path chosen, `=flet` → Flet path chosen (both via a seam that doesn't actually launch). 640 tests + SD74 snapshot stay green (no core touch).
- **No-console silent death** → early-failure path (log + dialog) verified in the windowed-exe smoke (force an import error, confirm log written + dialog shown + non-zero exit).
- **Worker marshalling done wrong** (cross-thread control mutation → freeze) → the loop-safe pattern is confirmed against the installed package and **recorded as a contract in the conventions doc** now; the code is built + tested at IA-5 against the real `run_pipeline` (avoids a mock-tested throwaway).
- **`run_pipeline` `sys.exit` + run-log asymmetry** → documented in the worker contract (the `SystemExit` path writes no run-log; `on_error` is the only failure signal) so IA-5 handles it correctly.
- **Coverage gate (80%) dip** from a new package → pure modules covered; only view glue omitted (coverage config lists specific files, not `src/ui_flet/*`).
- **Beta API drift** → exact pins + CI pin-assertion + conventions doc.

## Test strategy
- **Unit (covered, no display):** `app_version` (installed/"dev"); `tokens` (8 brand hex present + semantic aliases + **fg/bg contrast ≥4.5:1**); `theme` (M3 ColorScheme role mapping, light mode); `nav` model (the pure prominence model: unconfigured→Get-Started · configured-healthy→Everyday · etc. — tested even though the rail renders flat this slice).
- **Gate:** `test_flet_pin` — installed `flet`/`flet-desktop` == the single-source expected version AND `requirements.txt` + `FLET_1.0_CONVENTIONS.md` name that exact version (fails the build on drift).
- **Windowed-exe smoke (Win, local + repointed CI matrix):** build `src/ui_flet/launcher.py` with `flet pack`; assert PE subsystem=2 (no console) + offline-embed (reuse the PLAT-0b `ci_verify_pack.py` signal) + zero-orphan close (reuse the PLAT-0.3 harness) on the REAL launcher.
- **Regression:** full `pytest` + SD74 snapshot + ruff/mypy(non-UI incl. `src/ui_flet` excluded)/bandit + config-validate all green; architect core-untouched check (no diff under `src/etl|src/config|src/sftp|src/scheduler`, `run_pipeline`, CLI/exit codes).

## Decomposition (slices)
**ONE slice** — the plan-gate folded the original 1a/1b after the infra cuts (the residual was too small to stand alone; no half-done state):
- [ ] **PLAT-1 — Productionized shell + foundation** · pins (requirements/dev/pyproject) + `test_flet_pin` gate · `src/utils/version.py` (+ ROADMAP F2 line) · `tokens.py` (+contrast test) · `theme.py` · `nav.py` pure model (rail renders flat) · `launcher.py` (frozen-cwd + early-failure, paints-before-content) · `shell.py` (themed window + flat rail + plain `dict` of branded in-voice placeholders + zero-orphan close with a documented leave-point seam) · dual-mode opt-in env var (one-site read; `=flet` = dev default for later slices) · mypy/coverage config · tree · conventions-doc harvest (incl. the worker + FilePicker + `SystemExit` contracts) · windowed-exe smoke. *Lands complete:* a themed, opt-in, no-console Flet window with a flat rail + branded placeholders, zero-orphan clean close, gated pins, conventions doc finished — **no debt** (deferred infra is genuinely-separate IA-5/IA-1/PLAT-2 work, not debt).

---

## Review  _(synthesizer-gate, plan-gate altitude, Stage 3 — 2026-06-29)_

RUNNING AS: Opus 4.x (same-model run — this is a clean-context, separate-role pass on the most capable tier; a reduction of rubber-stamping risk, not a model-independent oracle).

- **Verdict:** **CHANGES REQUIRED** — the architecture is sound and the grounding is accurate (verified `main.py:188-194`, `main.py:196-199`, `pipeline.py:294/302/305` + `420-426`, `app_config.py:75-89`, parent 0013 line 55). The blocker is **scope, not correctness:** PLAT-1 builds three reusable engines + a close-guard ahead of any production consumer, against the program's own settled policy. Fix the scope, fold the four advisory refinements + the five product proposals, and this is approval-ready.

### Adjudication — YAGNI (OVER-BUILT) vs maintainability (CLEAN)

These two **do not actually conflict on the merits.** The maintainability-lens graded the plan CLEAN *with no structural blocker*; its only argument to **keep** `worker.py`/`filepicker.py`/`registry` is that they're "consumed by the shell demo." That consumer is **invented scope** — PLAT-1's own Non-goals (lines 19-21) state there are no real surfaces and the picker is "used in the shell demo" only. **A demo built solely to exercise infrastructure is not a production consumer; it is the over-build.** So the maintainability "keep" rests on the exact premise YAGNI refutes — once that premise falls, the two reviewers agree.

Three further factors make this decisive:
1. **Binding program precedent.** Parent plan 0013 line 55 dissolved DS-3 with *"promote to shared on 2nd use"* — already applied once in **this** program. The same rule governs `worker`/`picker`/`registry`: build them at their first real consumer (IA-5 for `JobRunner`+`FilePicker`, IA-1 for a real registry), not speculatively now.
2. **Rework risk against an imagined caller.** `JobRunner` built now is tested against a *mock* `run_pipeline` — but the real `PipelineResult` / internal-`sys.exit` / exit-3 SFTP-failure shape is only exercised at IA-5. Building the contract against an imagined caller is precisely what "2nd use" exists to prevent: the contract would likely be revised at IA-5 anyway, so the PLAT-1 version + its tests are throwaway.
3. **The close-guard has nothing to guard.** No write-in-flight surface exists until IA-5; loader atomicity (`save_all` backup-and-restore) is the real safety net. A guard with no in-flight writer is dead scaffolding.

**Ruling: YAGNI prevails.** Cut the speculative infra from PLAT-1; preserve the *findings* as contracts in the conventions doc (zero knowledge lost, zero throwaway code).

### Required changes (must revise before the Spec/approval gate)

1. **CUT `filepicker.py` from PLAT-1.** No file-picking surface exists this slice; the wrapper's first real consumer is IA-5 (FileChip) and its everywhere-migration is PLAT-2. Remove it from *Affected files* (line 52), the coverage-omit list (line 57), and the PLAT-1b decomposition (line 85). Update Non-goal line 21 — PLAT-1 ships **no** picker, not "the wrapper + shell-demo use."

2. **CUT the `worker.py` `JobRunner` core + its tests from PLAT-1; KEEP the contract as a doc artifact.** Defer the implemented engine to IA-5 (its first real `run_pipeline` caller). In PLAT-1, **harvest the marshalling contract into `FLET_1.0_CONVENTIONS.md`** instead: the worker→UI marshal snippet (the exact 1.0 loop-safe update mechanism confirmed against `flet==0.85.3`) **plus** the `SystemExit`/`Exception` handling contract (see change 6). Remove `worker.py` from *Affected files* (line 51), the worker tests from line 59 + the test strategy (line 77), and the PLAT-1b body (line 85). This keeps the #1 Flet correctness trap *documented and ready to reuse* without shipping code tested against a mock.

3. **CUT the close-guard scaffold; KEEP zero-orphan close + a documented seam.** Per Product P4: ship the proven zero-orphan close (PLAT-0-verified) **without** write-in-flight guard logic, but shape the close handler with a **documented leave-point seam/hook** so IA-2 (decouple-the-sync reassurance) and IA-5 (write-guard) attach without re-architecting. Reword "close lifecycle/guard scaffold" in `shell.py` (line 53) to "close lifecycle + documented leave-point seam (no guard logic)."

4. **TRIM the screen registry to a plain dict.** Replace the "Strategy-friendly screen registry" (lines 42, 50→nav, 85) with a plain `dict[destination → placeholder factory]`. A real registry is IA-1's call if ever needed. Reword the *Architecture & holistic fit* `maintainability-structure` bullet (line 37) — drop "Strategy-friendly screen registry"; it is YAGNI for one-liner placeholders.

5. **State WHO exercises the Flet path pre-CUT-1 (Product P1 — adopt).** The spec must name the anti-rot mechanism: **`DISTRICTSYNC_UI=flet` is the documented dev default for all subsequent UI slices (IA-1..CUT-1)**, so the shell can't silently rot across ~14 slices while Streamlit stays the user default. Add it to *Approach* and assert routing **both ways** in the dual-mode test (already partially in the risk row, line 69 — make the dev-default explicit).

6. **Normalize the worker `on_error` contract for the `SystemExit`/`Exception` asymmetry (maintainability F4 — confirmed at `pipeline.py:420-426`).** A caught `SystemExit` from `run_pipeline` (bad input) re-raises at line 421 **before** `_emit_run_log("failed", …)` — so **no `__DISTRICTSYNC_RUN__` "failed" record is written**; a caught `Exception` (422-425) **does** write one. The marshalling contract in `FLET_1.0_CONVENTIONS.md` (change 2) MUST document this: the UI's `on_error` is the *only* failure signal for the `SystemExit` path (Run History will show nothing), so the UI must not assume a run-log record exists on every failure.

7. **Read `DISTRICTSYNC_UI` at exactly ONE site (maintainability F3 — adopt).** The env-var switch is read only in `main.py`'s no-argv branch (line 45); never let it drift to a second source (e.g. the launcher re-reading it). State this as a one-site invariant in *Approach*.

8. **Placeholders are calm, branded, in-voice frames — NOT "coming soon"/raw TODO (Product P3 — adopt).** They set the tone every surface inherits. Reword the placeholder language in Goals (line 15), *Affected files* (line 53), and the decomposition so a placeholder is a branded "this surface arrives soon" frame in the product's reassuring voice, not a dev stub.

9. **Themed window chrome paints before content; early-failure dialog uses plain language (Product P5 — adopt).** Add to *Approach/Launcher*: the window paints themed chrome **before** content mounts (no flash of unstyled window), and the early-failure dialog shows plain language with the traceback routed to the **log only** (line 31 already logs the traceback — make the dialog-is-plain-language split explicit).

10. **Rail ships SIMPLE/flat now; defer grouped *prominence* to IA-1 (Product P2 + maintainability converge with YAGNI).** Keep the **pure nav-state model** (`nav.py`) — it's trust-critical and cheaply tested — but the *rendered* rail ships flat; grouped, state-aware **prominence** lands at IA-1 where there's actually something to be prominent about. Reword the IA-model coupling in Goals (line 15) + *Architecture* (line 42) so PLAT-1 doesn't render prominence with no signal to drive it.

**KEPT (the genuine PLAT-1 spine — do NOT cut):** entry wiring + the one-site env-var switch · the exact pins + `test_flet_pin` gate · `app_version()` in `src/utils/version.py` · `tokens.py` + the contrast ≥4.5:1 test · `theme.py` · `nav.py` **pure model** (rail renders flat) · `launcher.py` frozen-cwd + early-failure path (paints-before-content) · zero-orphan close + documented seam · the conventions-doc API harvest (now incl. the worker + `SystemExit` contracts) · the windowed-exe smoke.

### Sizing/completeness

**FOLD 1b into 1a — ship PLAT-1 as a SINGLE slice.** After cuts 1–4, the entire residual content of 1b is: `nav.py` pure model + a flat rail + one-liner placeholders + a plain placeholder dict + the conventions-doc harvest. That is small, has no half-done state, and is *complete* in one ≤1M-context session alongside 1a's foundation. The original XL→2-split was justified by the now-cut infra (worker/picker/registry/guard); remove the infra and the split is artificial overhead. Re-state the *Decomposition* as one slice: **PLAT-1 — Productionized shell + foundation**, *lands complete:* a themed, opt-in, no-console Flet window with a flat state-aware-model rail + branded placeholders, zero-orphan close with a documented leave-seam, gated pins, and the conventions doc finished. Merge the two Spec stubs (lines 98-103) into one; in-scope dimensions = `maintainability-structure`, `reliability-resilience` (early-failure), `testing`, `product-ux` (contrast + calm-placeholder tone).

*Slice-completeness checks:* foundation+entry — **OK** (complete, gated). Shell-infra (post-cut) — **OK, fold into the single slice** (too small to stand alone). No slice leaves debt after the cuts; the deferred infra is genuinely-separate future work (IA-5/IA-1/PLAT-2), not debt.

### Harness impact

- **F2 — track the `main.py:196-199` version-DRY follow-up as a real backlog line, not just a plan mention.** PLAT-1 adds `app_version()` in `src/utils/version.py` but leaves the CLI's inline `importlib.metadata.version(...)` duplicated (deliberately, to keep the CLI branch untouched). Add a one-line `docs/claugentic-ROADMAP.md` entry — *"DRY `main.py:196-199` onto `src/utils/version.py:app_version()` (deferred from PLAT-1 to keep the CLI branch untouched)"* — so the duplication is tracked, not silently accepted. A plan mention alone is lost at plan deletion (Stage 8).
- **Conventions-doc contract capture (replaces shipped code).** The cut `JobRunner` + `FilePicker` are preserved as **contracts in `FLET_1.0_CONVENTIONS.md`**: the worker→UI marshal snippet, the `SystemExit`/`Exception` `on_error` asymmetry (change 6), and the FilePicker-as-async-service form. This is the "promote on 2nd use" discipline operationalized — knowledge captured now, code built at first real use.
- **F6 — `shell.py` boundary note (advisory, not a blocker).** `shell.py` carries window + rail + placeholder-host + close-lifecycle (4 concerns). Acceptable for PLAT-1's size, but record a one-line note in the plan that this is a **boundary to split before IA surfaces grow it** (IA-1 is the natural point). Not a Stage-3 blocker — flagging it forward so it doesn't calcify.
- **DECISIONS line at Land.** The "cut speculative infra from PLAT-1 per the DS-3 'promote on 2nd use' precedent; captured as conventions-doc contracts" call is a non-trivial scoping decision — record it as a dated `docs/claugentic-DECISIONS.md` one-liner when PLAT-1 lands.
- **No new STANDARD or agent** implied by this slice.

---

## Spec  _(single slice — Stage 4; for the approval gate)_
### PLAT-1 — Productionized shell + foundation
- **In plain English (read this first):** I'll build a branded native DistrictSync window — the *frame* every future screen drops into — reachable by setting `DISTRICTSYNC_UI=flet`. **Nothing changes for today's users:** Streamlit stays the default UI; the CLI, the nightly scheduled sync, and exit codes 0/1/2/3 are untouched. The window opens already-themed (no blank flash), shows a left navigation with calm, branded "this surface arrives soon" placeholders, and **closes cleanly with zero leftover processes**; if it ever fails to start it shows a plain-language error (details to the log) instead of dying silently. It ships as a one-file **no-console** exe, and a build gate stops the Flet version from silently drifting. **Done =** themed window opens + closes zero-orphan + windowed/offline exe + all gates green + the conventions doc finished. **You're accepting:** a new `src/ui_flet/` package + the Flet pins; placeholder screens (real surfaces are IA-1+); the reusable worker/picker engines are *documented as contracts now and built when first used* (IA-5/PLAT-2), not pre-built here.
- **In-scope standards dimensions:** `maintainability-structure` (layering, pure/view split) · `reliability-resilience` (early-failure, paints-before-content, zero-orphan close) · `testing` (pure-logic coverage + flet-pin gate + windowed smoke) · `product-ux` (contrast ≥4.5:1 + calm-placeholder tone).
- **Build steps (checklist):** ☐ pins + `test_flet_pin` · ☐ `app_version()` + ROADMAP(F2) line · ☐ `tokens.py` + contrast test · ☐ `theme.py` + test · ☐ `nav.py` pure model + test · ☐ `launcher.py` (frozen-cwd, early-failure, paints-before-content) · ☐ `shell.py` (themed window, flat rail, branded placeholders, zero-orphan close + leave-seam) · ☐ `main.py` one-site env switch + routing test (both ways) · ☐ mypy/coverage/tree config · ☐ conventions-doc harvest (worker + FilePicker + `SystemExit` contracts) · ☐ windowed-exe smoke (PE subsystem 2 + offline + zero-orphan on the real launcher).
