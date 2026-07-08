# 0029 — Flet UI trust & professionalism redesign

- **Status:** Draft
- **Resumable from:** Stage 2b advisory panel → 2c incorporate → Stage 3 review
- **Blockers:** none
- **Flags:** `app-data relocation to %LOCALAPPDATA% proposed as opt-in Slice 10 — user decision pending`
- **Disposition at close:** per template
- **Roadmap item:** docs/claugentic-ROADMAP.md → "0029 Flet UI trust & professionalism redesign"
- **References:** `docs/claugentic-ARCHITECTURE_TREE.md` · `docs/claugentic-DECISIONS.md` · plans 0012/0013 (git history) · session investigation wf_84cdd881-3a2 (46-agent verified findings, 2026-07-08)

## Problem

Shan's 2026-07-08 field test of the released v3.4.0 Flet UI surfaced ~12 verified root-cause defects plus a product-level gap: the UI was ported for feature parity (Plan 0013) and repeatedly **asserts state it never verified**. DistrictSync is a trust instrument — a district admin must believe the nightly sync works without watching it — and every defect below breaks that trust. All claims verified with file:line evidence by independent adversarial checkers:

1. **Stale AppConfig snapshot** — `shell.py:170` loads `AppConfig` once at startup and binds the instance into Home/Run History/Mapping/Help via `functools.partial`; screens rebuild per navigation but from the frozen snapshot. District switch never propagates until restart; the Mapping screen's own "current mapping" card stays stale (`mapping.py:124` vs `_on_apply` at `mapping.py:165` which loads a *separate* fresh instance); the Apply gate (`mapping.py:145-147`) compares against the stale value so a user cannot revert a switch without restarting; Home's onboarding branch (`home.py:143`) never leaves "not set up yet" after in-session setup. Setup/Convert already load fresh (`setup.py:94`, `convert.py:147`) — the pipeline always used the right config; this is a display/trust defect, not data corruption.
2. **Run history store is not production-grade** — Run History parses `__DISTRICTSYNC_RUN__` lines from the rotating diagnostic log (`run_log.py:58` reads only the live `etl_tool.log`, never `.1/.2/.3`; 5MB×3 rotation silently erases history; RotatingFileHandler is not multi-process-safe against the scheduled CLI writer on Windows). Worse, **pytest pollutes the real user log**: `src/main.py:47` runs `get_logger` at import time and `tests/conftest.py` has no path isolation — 365 of the 370 records in Shan's live log are test fixtures. Free-text `error=str(e)` (paths/columns) is persisted (`pipeline.py:244`).
3. **Schedule state divergence** — `setup.py:303` persists `schedule_time` + `schedule_registered` **before** `register_task` runs and never reverts on failure; status everywhere (`home_status.py:257`, `nav.py:74`, Run History footer) trusts the config boolean and never reconciles with the real task (`query_task` is dead code that also omits `/V`, so its promised last-run fields can never appear — `windows.py:544-565`). Live proof: config says "scheduled 15:36" while the task was externally deleted (Event 141) — the app will report "scheduled" forever while nothing runs. "Save setup" (`setup.py:133`) silently ignores the schedule section (two save buttons, undisclosed scopes). No unregister affordance exists (`delete_task` has zero callers). Registered action path is pinned to wherever the exe ran from (e.g. `Downloads\`) with no warning.
4. **Elevation dead-end** — unattended registration (Password logon + `RunLevel Highest`, `windows.py:260-266`) genuinely requires an elevated caller; the only affordance is "quit and re-open as administrator" (`setup_errors.py:57-61`). Whole-app elevation is wrong for a PII app (UAC every launch, mapped-drive loss, breaks non-interactive task launch of a `requireAdministrator` exe).
5. **SFTP test trust gaps** — Test is real (live paramiko auth verified) but: it **writes a typed password to the keyring before testing** (`setup.py:587-588`), so a typo'd password clobbers a working credential even when the test fails; it tests live unsaved form fields (`setup.py:471-477`) while the nightly uses saved config (`pipeline.py:447-451`); success copy (`setup.py:628-629`) is provenance-blind — never names host, user, or that a stored credential was used.
6. **Nav model** — state-dependent destination reordering (`nav.py:100-114`) destroyed spatial memory and read as instability to the first real user; programmatic navigation never moves the rail highlight (`shell.py:215-217` — `build_nav` discards the rail reference by design, `shell.py:14-15`), desyncing all three `on_navigate` call sites (onboarding CTA, Home fix buttons, error fallback).
7. **Silent district defaults** — `AppConfig.sis_type` defaults to `"myedbc"` (`app_config.py:30`) and comes pre-selected in Setup (`setup.py:170`), so a new user can Save without choosing a district and silently run the generic base mapping; Convert falls back to `configs[0]` alphabetically (`convert.py:253`).
8. **Convert output dead-end** — success view (`convert.py:396-422`) never shows where files were written and offers no open-folder affordance; the PII "never render a raw path" rule (`convert_result.py:15-20`) is over-applied to a local, app-owned config path already visible in Setup and the local log. Empty `output_dir` silently falls back to the *input* dir (`convert.py:147`).
9. **Chrome/input craft** — Exit button is a total no-op: Flet 0.85.3 `Window.destroy()`/`close()` are `async def` (`site-packages/flet/controls/core/window.py:355/367`); `shell.py:223`, `shell.py:243` (latent), and `launcher.py:104` call them synchronously (un-awaited coroutine; no exception, so the `os._exit` fallback never fires). No `page.window.icon`, no brand `.ico` asset, no `--icon` on either `flet pack` invocation (Makefile:37-76, `.github/workflows/flet-pack.yml`). No `TextField` wires `on_submit` (all 7 fields in setup.py) — Enter does nothing.
10. **Onboarding triple-door** — while unconfigured the user faces three entry points to one job: Setup-led nav order, a Setup tab, and a Home onboarding hero whose only control points at Setup.

## Goals / Non-goals

- **Goal (product bar):** the UI never asserts a state it hasn't verified; every success names what it verified; "where am I" and "where are my files" always answerable; setup ends in a *verified* finish line; run history is a clean, district-scoped, durable ledger; the app looks and behaves like a professional myBlueprint application (brand icon, Enter works, Exit works, stable nav).
- **Goal:** first-run Setup is a stepped wizard (Folders → District → Schedule → Delivery → verified summary) that graduates into a flat Settings surface for edits (user-approved direction).
- **Goal:** schedule registration self-elevates per-operation via a normal UAC prompt (user-approved direction); the app itself stays non-admin.
- **Goal:** dedicated run-history store (SQLite, per-user app-data dir) with test isolation, replacing log-parsing; Task-Scheduler read-back as the schedule source of truth.
- **Non-goal:** YAML mapping editor (IA-8b, stays ROADMAP), Linux/macOS elevation parity (cron path unchanged), any change to ETL transform semantics or output CSV contracts (SD74 snapshot must stay byte-identical), multi-user/server features, auto-update.
- **Non-goal:** redesigning the visual design system (tokens/theme/components stay; we compose them).

## Approach

**One combined redesign program** (user-chosen over fixes-first) sliced so every slice lands complete. Trust-critical logic continues the established split: pure COUNTED modules (tested) + thin view glue (coverage-omitted).

Key design decisions:

- **D1 — Config freshness via load-per-build, not a reactive store.** Screens already rebuild per navigation; the fix is to bind `AppConfig.load` (a supplier) instead of a loaded instance — the pattern Setup/Convert already use. A pub/sub AppState was considered and rejected (YAGNI: rebuild-per-navigation makes freshness free; the only same-surface refresh needed is Mapping's post-Apply re-render and the shell's nav-badge update, both local). Shell recomputes `needs_setup`-derived UI on each navigation.
- **D2 — Run store = SQLite (`history.db`, WAL) in the per-user app-data dir.** Written by the pipeline alongside the existing log line (log stays purely diagnostic); schema-versioned; fields: schema_version, timestamp, sis_type, source (`manual`/`scheduled`/`cli`), status, duration, entity counts, sftp fields, anomaly count, data-error summary, sanitized `error_category` (never free text). Read side repoints `read_run_records`-equivalent with the same `[] / None` degradation contract; one-time best-effort backfill from existing log lines (tagged `source=unknown`). SQLite over runs.jsonl because two independent processes (open UI + scheduled CLI) write concurrently — WAL gives real multi-writer safety; single file, zero dependencies (stdlib `sqlite3`), industry-standard for desktop apps. **Nothing is ever written next to the exe, and never inside it** — a running signed executable cannot and must not modify itself; per-user app data is the professional Windows convention.
- **D3 — pytest isolation is a correctness fix, not test hygiene:** autouse conftest fixture pointing `paths.user_data_dir`/`user_log_file` at `tmp_path`, plus moving `get_logger` out of `src/main.py` import time into the entry paths. No test may ever touch the real profile again.
- **D4 — Schedule truth = Task Scheduler read-back.** Replace dead `query_task` with a PowerShell `Get-ScheduledTaskInfo`-based reader (structured, locale-independent: exists, next_run, last_run, last_result, action path). Home/Setup/Run History derive schedule status from it (config boolean demoted to hint); Setup shows "Currently registered: daily at HH:MM — next run <t>" and gains Unregister. Config is written only **after** successful registration.
- **D5 — Per-operation elevation:** `ShellExecuteExW(lpVerb="runas")` on `powershell.exe -EncodedCommand` with `SEE_MASK_NOCLOSEPROCESS` → `WaitForSingleObject` + `GetExitCodeProcess`. Since an elevated child inherits neither env nor stdio: parent passes `DSYNC_*` values (incl. the password) via a DPAPI user-scope-encrypted temp file (same-user SID decrypts) and receives `{ok, message}` back the same way; file is random-named in `%LOCALAPPDATA%`, deleted in `finally`. UAC-declined = `ERROR_CANCELLED` (1223) → calm "you declined the prompt" copy. `classify_schedule_error` unchanged downstream. Non-elevated no-password (Interactive/Limited) path stays direct (no UAC needed).
- **D6 — SFTP test becomes side-effect-free and provenance-honest:** `test_connection(password_override=...)` passes a typed password transiently (keyring written only on explicit Save); success copy states host + username + credential source ("the password saved in this computer's credential manager" vs "the password you just entered — click Save to keep it"); unsaved host/user edits soften the claim ("these settings work — Save to use them for the nightly sync").
- **D7 — Nav: fixed order always** (Home, Convert, Run History, Setup, Mapping, Help); newcomer guidance via initial selection (launch on Setup wizard while `needs_setup`) + a "needs attention" badge on Setup, never reordering. `build_nav` returns/exposes the rail so `select_by_id` syncs the highlight — one change fixes every programmatic hop.
- **D8 — Wizard→Settings hybrid:** wizard state machine as a pure COUNTED module (steps, gating, verified-finish derivation); view renders it. Finish line is *verified*: folders validated (existing gates), district explicitly chosen, schedule confirmed via D4 read-back, delivery confirmed via D6 test — ending in "Tonight at HH:MM, DistrictSync builds <district> and delivers to SpacesEDU." Completed setup renders the flat Settings scroll with **one** Save that reconciles (re-registers the task when the time changed and a schedule exists).
- **D9 — Explicit district everywhere:** `sis_type` default `""`; "Choose your district" placeholder; auto-select only when exactly one config exists; Convert refuses to run without an explicit district (no `configs[0]` fallback). Existing installs are unaffected (config already has a value).
- **D10 — Convert output visibility within the PII rule:** `ConvertResult` stays path-free (pure model); the *view* renders the resolved output folder (app config, not student PII) + "Open folder" (`os.startfile`); the resolved output dir is shown on the form *before* running; empty `output_dir` no longer falls back to the input dir — Convert blocks with a routed "set your output folder in Setup" message.

Alternatives rejected: whole-app `--uac-admin` (PII posture, mapped drives, breaks scheduled launch); runs.jsonl (weaker concurrent-writer story); reactive config store (YAGNI); filtering the existing log at read (rotation loss + pollution remain); "restart as admin" relaunch (loses typed state, session stays elevated).

## Architecture & holistic fit

- **Codebase fit:** preserves the three-layer split (UI / ETL-business / config-data) and the COUNTED-pure vs view-glue convention. New module `src/history/store.py` (data layer: SQLite run store, consumed by `pipeline.py` for writes and by a pure UI-side reader for reads) — placed outside `ui_flet` because the CLI/scheduled path writes to it too. Scheduler read-back + elevation live in `src/scheduler/windows.py` (single Windows-integration seam; validators still gate all inputs). Wizard state machine = new pure module `src/ui_flet/setup_flow.py` (COUNTED), rendered by `screens/setup.py`. Nav fix stays in `nav.py`/`nav_rail.py`/`shell.py`. No new dependencies (stdlib `sqlite3`, `ctypes`).
- **Product fit:** direct implementation of the Discover deliverable (trust instrument; Installer/Watcher/Firefighter personas) — to be persisted to `docs/claugentic-PRODUCT.md` in Slice 9 after user approval of this plan.
- **Quality dimensions to uphold:** `data-and-persistence` (run store: schema versioning, WAL, migration/backfill, atomicity), `reliability-resilience` (read-back degradation, elevation-declined path, store-unreadable sentinel), `security` (DPAPI handshake, password never on argv/env-of-elevated/argv-visible, keyring write only on Save, validators on all task inputs), `privacy-pii` (error_category not free text in store; paths only local-view-layer), `observability-ops` (run records enriched with source/district; log stays diagnostic), `product-ux` (verified states, one front door, stable nav, keyboard), `testing` (pytest isolation, pure-module coverage, SD74 snapshot untouched), `maintainability-structure` (COUNTED/view split, single-source status derivation).
- **Future-proofing:** run store schema_version enables later fields (e.g. per-entity deltas); `source` tag enables later filtering UI; read-back seam is Windows-only behind the existing platform split (Linux cron read-back can slot in later); wizard steps are data-driven for future steps (e.g. attendance tier). Not building now: history export, multi-district ledgers, auto-update.

## Affected files

- `src/ui_flet/shell.py` — supplier-based config binding; rail reference + highlight sync; async exit; `page.window.icon`; nav-badge refresh.
- `src/ui_flet/nav.py` / `nav_rail.py` — fixed order; badge model; expose rail/selection setter.
- `src/ui_flet/screens/mapping.py` — write-through apply + self-refresh; gate vs persisted value.
- `src/ui_flet/screens/home.py` / `help.py` / `run_history.py` — fresh config per build; Home schedule verdict consumes read-back; Run History reads the new store (district-scoped, source-tagged).
- `src/ui_flet/screens/setup.py` — wizard/settings hybrid (rendering `setup_flow`); Enter-to-submit; SFTP test copy + side-effect-free test; save-after-success; unregister; single Save reconcile.
- `src/ui_flet/setup_flow.py` (new) — pure wizard state machine + verified-finish derivation.
- `src/ui_flet/screens/convert.py` / `convert_result.py` — output folder display + Open folder; explicit district; no input-dir fallback.
- `src/ui_flet/run_log.py` / `home_status.py` / `run_history.py` — reader repoint to store (same `[]/None` contract); precedence gains "task missing".
- `src/history/store.py` (new) — SQLite store (write/read/backfill/compaction).
- `src/etl/pipeline.py` — write run record to store (+ `source` param); error_category mapping.
- `src/scheduler/windows.py` — `query_task` rewrite (Get-ScheduledTaskInfo); elevation runner + DPAPI handshake; register returns structured result.
- `src/utils/paths.py` — `user_history_db()`; (optional Slice 10: app-data relocation + migration).
- `src/main.py` — `get_logger` out of import time; CLI passes `source`.
- `tests/conftest.py` — autouse profile isolation fixture; new tests per slice.
- `assets/districtsync.ico` (new) + `Makefile` + `.github/workflows/flet-pack.yml` — brand icon end-to-end.
- `src/ui_flet/launcher.py` — async close fix in error dialog.
- `docs/` — ARCHITECTURE_TREE (new files), DECISIONS (D1–D10), PRODUCT.md (Discover persistence), partner docs refresh.

## Research / grounding

- **Files reviewed:** all file:line citations in Problem (from the 46-agent investigation, each claim independently verified: 34 Verified, 1 Unconfirmed-latent); Flet 0.85.3 installed source (`window.py:355/367`, `textfield.py:593`, `base_control.py:450`, `pack.py:243-308`); live machine state (config.json, etl_tool.log, TaskScheduler event log Events 106/140/141, keyring existence, output dir).
- **Harness docs consulted:** CLAUDE.md (Flet 1.0 conventions pointer, engineering principles, scope tiers), `docs/claugentic-WORKFLOW.md` (this pipeline), `docs/FLET_1.0_CONVENTIONS.md` referenced for view work.
- **Findings:** `-Force` already present (re-registration was never broken — the GUI needed F5); `delete_task`/`query_task` exist unused; Setup/Convert already model the fresh-load pattern; `filepicker.py` save-gate reusable in wizard; `verdict`/`humanize`/`home_status` single-source modules extend cleanly.

## Risks & mitigations

- **SD74 snapshot / output contract regression** → non-goal fence: zero transform/loader changes except pipeline run-record write (additive); snapshot + contract tests must stay green every slice.
- **SQLite on roaming/network profiles (WAL + SMB)** → district servers use local profiles; store opens with WAL and falls back to `DELETE` journal on failure; reader keeps the `None` graceful-degradation sentinel.
- **Elevation handshake is hard to CI-test** → pure parts (blob encode/decode, script build, result parse) unit-tested with DPAPI mocked; one manual verification checklist item at Verify (real UAC prompt, declined path, success path). Password must never appear on argv, in the parent env, or in any log — audited at Verify (`honesty-reviewer`/`security` lens).
- **Backfill misparses old log lines** → best-effort, tagged `source=unknown`, never blocks store creation; failures logged, not raised.
- **Wizard scope creep** → wizard = existing Setup sections re-sequenced with verified gates; no new configuration surface. `yagni-sentinel` reviews.
- **Flet 0.85.3 API traps** (async handlers, `on_select` vs `on_change`, `content=`) → conventions doc + view changes exercised via the runtime-qa role at Verify.
- **Existing deployed installs** (SD40–74 on v3.4.0) → config migration is additive (missing fields default); store is created lazily; empty-`sis_type` default only affects fresh installs.

## Test strategy

- New: store round-trip/schema/compaction/concurrent-writer tests; backfill parser; `query_task` output parsing (fixture-driven); elevation blob + result-file protocol (mocked DPAPI/ShellExecute); `setup_flow` state machine (all step/gate transitions, verified-finish derivation); nav fixed-order + badge model; mapping apply/revert gate; convert district gating + output-dir resolution; SFTP `password_override` pass-through + no-keyring-write-on-test; Enter-submit handlers re-check gates.
- Isolation: autouse tmp-profile fixture proves no test writes to the real user dir (assert real path untouched).
- Regression: full suite + SD74 snapshot + contract tests + `make validate-config` green per slice; coverage gate 80% holds (pure modules COUNTED, view glue omitted per existing pyproject policy).

## Decomposition (slices)

- [ ] **Slice 1 — Config freshness + mapping integrity** (D1): supplier-based binding in shell; fresh load in home/help/run_history/mapping; mapping write-through apply + self-refresh + correct gate. Lands complete: every stale-display symptom gone; tests for gate/revert.
- [ ] **Slice 2 — Chrome & input craft** (part of D7 + fixes): async exit (shell ×2 + launcher), Enter-to-submit with gate re-checks, brand `.ico` + `page.window.icon` + `--icon` in both builds. Lands complete: exit/enter/icon all work in dev + frozen.
- [ ] **Slice 3 — Run store** (D2, D3): `src/history/store.py`, pipeline write + `source` tag, reader repoint, backfill, pytest profile isolation, `get_logger` deferral. Lands complete: Run History reads the store; tests can never pollute a real profile.
- [ ] **Slice 4 — Schedule truth** (D4): `query_task` rewrite, status derivation from read-back (Home/Setup/Run History), save-after-success, Unregister, action-path warning. Lands complete: a deleted task is detected and routed to re-register.
- [ ] **Slice 5 — Per-op elevation** (D5): elevation runner + DPAPI handshake + declined-path copy; wired into register flow. Lands complete: un-elevated registration shows one UAC prompt and succeeds, or reports honestly.
- [ ] **Slice 6 — SFTP truthfulness** (D6): side-effect-free test, provenance-named success/failure copy, Save-only keyring writes. Lands complete: failed test can no longer clobber a credential.
- [ ] **Slice 7 — Setup wizard→settings hybrid** (D8, D9 setup-side): `setup_flow.py` + wizard rendering + verified finish line + settings mode with single reconciling Save + empty default district. Depends on 1,4,5,6. Lands complete: first-run reaches a verified "you're set up" summary.
- [ ] **Slice 8 — One front door + Convert output** (D7 nav, D10, D9 convert-side): fixed nav + badge + rail-follow; unconfigured Home collapses to the single hero→wizard door; convert output-dir display + Open folder + explicit district + no input-dir fallback. Lands complete: no desynced highlight, no output dead-end, no silent district.
- [ ] **Slice 9 — Close & persist** : PRODUCT.md persistence, DECISIONS entries (D1–D10), ARCHITECTURE_TREE/CHANGELOG/partner docs, whole-feature closing Verify (Stage-7 last-slice pass against the Stage-1 job-to-be-done), retrospect harvest.
- [ ] **Slice 10 (OPT-IN, user decision pending)** — relocate app data `~/.districtsync` → `%LOCALAPPDATA%\DistrictSync` with transparent one-time migration (config, store, log; keyring unaffected). Windows-native convention; touches `paths.py` single-source + scheduled-task docs.

---

## Review  _(filled by synthesizer-gate in its plan-gate altitude, Stage 3)_
- **Verdict:** _pending_
- **Required changes:** _pending_
- **Sizing/completeness:** _pending_
- **Harness impact:** _pending_

---

## Spec  _(per slice, after Review passes — Stage 4, JIT per slice)_
