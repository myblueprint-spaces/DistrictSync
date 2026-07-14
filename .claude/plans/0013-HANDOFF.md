# Plan 0013 — implementation handoff (paste into a fresh session)

> Copy the block below into a new, full-context Claude Code session in this repo to begin implementation. The discovery/decision/planning work is done and committed to these files; a fresh session starts the build.

---

We are implementing the **DistrictSync UI production-redesign on Flet 1.0** (native desktop, replacing Streamlit). The architecture is **LOCKED** and the program is planned + adversarially reviewed. Do **not** relitigate Flet vs NiceGUI vs pywebview vs Streamlit.

**Read these first, in order:**
1. `docs/claugentic-DECISIONS.md` — the top entry (2026-06-29) records the locked Flet decision + rationale + version-matched docs links.
2. `.claude/plans/0013-flet-production-redesign.md` — the program plan. **Read the "Scope locked (2026-06-29)" block carefully** — it overrides the slice table where they differ (it defers the full config editor IA-8b and all production-extras OPS-* to the ROADMAP; keeps Win+Linux+macOS; sets code-signing = SignPath Foundation free).
3. `docs/FLET_1.0_CONVENTIONS.md` — **READ BEFORE WRITING ANY FLET CODE.** Flet 1.0 is a ground-up rewrite; your training data mostly describes the old `0.2x` API. Anchor to this doc + https://flet.dev/docs, not old tutorials.
4. `docs/reference/flet-prototype-spike/` — the **working bake-off prototype** (`app.py` + `NOTES.md`): a proven Home + Convert in Flet 1.0 (shell, `ft.Theme` brand mapping, `ft.FilePicker`, async-run-on-thread, clean-close lifecycle). Use as reference. Throwaway — delete after PLAT-1.

**How to work:** Follow the claugentic harness workflow (`docs/claugentic-WORKFLOW.md`) — implement slice-by-slice (plan → spec → build → verify), each landing complete behind all gates (pytest + SD74 snapshot + tree-check + ruff/mypy(non-UI)/bandit + config validation) with an **architect core-untouched check**. Update `docs/claugentic-ARCHITECTURE_TREE.md` in the same change that adds any `src/**/*.py` file.

**Hard constraints (non-negotiable):**
- **Presentation-only.** Reuse the tested ETL/CLI core UNCHANGED (`src/etl`, `src/config`, `src/sftp`, `src/scheduler`, `src/main.py` CLI branch — 640 tests, 80% gate, SD74 snapshot must stay green). Call `run_pipeline(...)` in-process on a **worker thread**.
- **Dual-mode entry preserved:** `src/main.py` `__main__` — only the `len(sys.argv)==1` (UI) branch changes (Streamlit launcher → `src/ui_flet/launcher.py`); CLI + flags + exit codes 0/1/2/3 untouched. New Flet code is additive under `src/ui_flet/`.
- **Streamlit `src/ui/` stays intact as the rollback floor until the final CUT-1 slice.**
- **Pin Flet exactly** (`flet==0.85.3` + `flet-desktop` + `flet-web`, confirm in PLAT-0) and add a CI assertion that the pin matches `docs/FLET_1.0_CONVENTIONS.md`.
- **Gate catch to honor:** `_classify_schedule_error` lives in `src/ui/pages/01_Setup_Wizard.py` (NOT the scheduler core) — when porting the Setup Wizard (IA-4), relocate it (and its `importlib`-by-path test) to a presentation-neutral module so CUT-1's `rm src/ui` doesn't orphan it.

**Start with PLAT-0 (a throwaway spike), then PAUSE for my approval before PLAT-1.** PLAT-0 objectives on this Win11 machine:
1. Resolve + lock the exact `flet`/`flet-desktop`/`flet-web` version pin.
2. Validate `flet pack` (or PyInstaller `--windowed`) produces ONE **windowed, no-console** exe that **bundles the Flutter client (offline)**; record bundle size.
3. Re-confirm native window-close → **zero orphan processes** (a scratch venv with Flet 0.85.3 + `psutil` exists at `C:/Users/shan.peiris/dssp/.venv`; or make a fresh one — **use a SHORT venv path**, Windows MAX_PATH bit us at deep scratch paths).
4. Decide the offline Flutter-client pre-seed approach for CI.
Record results so PLAT-0 makes `docs/FLET_1.0_CONVENTIONS.md` authoritative; then PLAT-1 builds the productionized shell.

**Do NOT build (deferred to ROADMAP):** the full 7-step config editor (IA-8b) and the production-ops extras (Check-My-Setup / Export-Diagnostics / update-check) — they follow a later config-capability epic. Ship only IA-8a (select a pre-built config) for the Mapping surface.

---

_Context: this plan came out of a UX discovery review (Plan 0012) → a Flet-vs-NiceGUI bake-off (both passed an empirical window-close graceful-shutdown test; Flet chosen for pure-Python/no-web-stack + offline self-contained renderer) → this Flet program plan (0013), adversarially reviewed (YAGNI trimmed, plan-gate CHANGES_REQUIRED all incorporated)._
