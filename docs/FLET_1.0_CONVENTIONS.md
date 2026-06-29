# Flet 1.0 — conventions & gotchas (DistrictSync)

> **STATUS: DRAFT** — seeded from the 2026-06-29 Flet-vs-NiceGUI bake-off + empirical verification. Slice **PLAT-0** finalizes the exact version pin and **PLAT-1** makes this authoritative (harvesting `docs/reference/flet-prototype-spike/NOTES.md`).
>
> **READ THIS FIRST before writing ANY Flet code for DistrictSync.** Flet 1.0 is a **ground-up rewrite**; the vast majority of Flet tutorials, StackOverflow answers, and model training data describe the **OLD `0.2x` API**. Each rule below pairs the 1.0 way with the **❌ 0.2x trap** so you don't regress to remembered patterns. Anchor to **this doc + the version-matched docs** (https://flet.dev/docs — getting-started / controls / reference), **never** old tutorials.

## Pinned versions (exact)
- `flet==0.85.3`, `flet-desktop==0.85.3`, `flet-web==0.85.3` — the latest **1.0 BETA** line as of 2026-06-29. (PLAT-0 confirms/locks; a CI assertion should verify the pin is exact and matches this doc.)
- **❌ GOTCHA (verified):** bare `flet` runs **neither** native **nor** web — you MUST also install **`flet-desktop`** (native window) **and** `flet-web`. In the bake-off the web server never bound a port until `flet-web` was installed.
- It is **beta** — the API can still shift before 1.0-final. Pin exactly; a ROADMAP trigger tracks the bump to stable.

## Entry point
- `ft.run(main)`.  **❌ NOT** the 0.2x `ft.app(target=main)`.
- `main(page)` builds the page; **native desktop is the default**. Web mode (for headless screenshots / CI) is gated behind an env flag in the prototype (`SPIKE_WEB`) — see the reference app.

## Controls are typed dataclasses
- 1.0 controls are **typed Python dataclasses** with strongly-typed event handlers and docstrings — the **installed package is itself the canonical API reference** (introspect it; don't trust old web examples).
- **❌ 0.2x traps:** the convenience helpers `ft.padding.all()/symmetric()` and `ft.border.*` were removed/restructured into dataclass forms; `SnackBar` is shown via `page.show_dialog`-style APIs; `page.window.*` methods (e.g. `center()`) are **async**. Confirm each exact form against the installed package in PLAT-1 and record it here.

## FilePicker is an async SERVICE (replaces tkinter)
- 1.0 `FilePicker` is a **service** with an **async-returns-files** API: register it (page services/overlay), call `get_directory_path()` / `pick_files()` and **await / handle the returned result**.  **❌ NOT** the 0.2x `on_result` callback.
- Returns a **real server-side filesystem path** (the UI runs on the district server) — a direct, better replacement for `src/ui/folder_picker.py`'s tkinter dialog (drops the tkinter hidden-import).

## Worker-thread → UI marshalling (THE #1 correctness trap)
- The ETL core (`run_pipeline`) is **synchronous/blocking** (pandas) → run it on a **worker thread** (`page.run_thread` / `threading.Thread`) so the window never freezes.
- **Hand UI updates back to the Flet event loop** per the 1.0 convention — **never mutate controls cross-thread**, or the window corrupts/freezes. PLAT-1 nails the exact mechanism in `worker.py` and records the canonical snippet here. Every async surface reuses it.

## Lifecycle (VERIFIED this session — the headline win)
- **Native OS window-close terminates the whole process tree cleanly: ~0.5s, ZERO orphans** (verified by sending `WM_CLOSE` + a `psutil` process-tree check). This is *why* Flet was chosen — **no idle watchdog, no beacon, no zombie-websocket reaping, no "close this tab" page** (all the Streamlit-in-browser workarounds are deleted).
- Flet binds an **internal localhost port** (random, e.g. `127.0.0.1:57347`) for the Python↔Flutter-client channel — **not** browser-facing; it closes with the window.
- Add a **confirm-if-write-in-flight** guard (reuse the `write_guard` concept) so closing mid-conversion can't tear the loader's atomic commit. The guard is a UX courtesy; the loader's backup-and-restore atomicity is the real safety net.
- **Single-instance guard: DEFERRED** (YAGNI) — add only if field reports show double-launch confusion.

## Packaging (windowed · offline · signed)
- `flet pack` (or PyInstaller `--windowed`/`--noconsole` + the Flutter client data) → **ONE windowed exe, NO console** — this kills the original "console window hangs around" bug.
- **VERIFIED:** `flet pack` **bundles the Flutter client into the exe** → the shipped app needs **no runtime download** and **works offline**. Exe ≈ 90–150 MB.
- **❌ GOTCHA:** the **first run *from source*** downloads a ~90 MB Flutter client from GitHub into `~/.flet` (one-time, cached) — irrelevant to the packaged exe but slow in dev/CI (this is why the bake-off's first native launch took ~22s vs ~2s cached). Pre-seed in CI or accept the one-time delay.
- **No-console builds hide boot errors** → add an **early-failure path** (write to `etl_tool.log` + show an error dialog) **before** the shell mounts; verify in the PLAT-1 smoke. Sanity-check bundle size + endpoint-AV behavior there too.
- **Code-signing:** **SignPath Foundation (free)** — the repo is public/OSS so it qualifies; signs in GitHub Actions (cert from Sectigo). Caveats: SmartScreen publisher shows **"SignPath Foundation"**; SmartScreen reputation **builds over downloads** (only an EV cert clears it instantly). **Azure Artifact Signing (~$10/mo)** is the cheap-paid upgrade for *your* org name later. See [DECISIONS 2026-06-29](claugentic-DECISIONS.md) + Plan 0013 "Code-signing reality".

## Theming (brand → ft.Theme)
- Port the brand **values** from `src/ui/brand.py` (`#1D5BB5` primary · `#0F2D6B` navy · `#0EA5E9` sky · `#16A34A` green · `#F0F6FF` page tint · `#DBEAFE` border · `#0F172A` text · `#64748B` muted) into a tiered Python token module → one `build_theme()` → `ft.Theme(Material-3 ColorScheme)`.
- **❌ Do NOT port** `brand.py`'s ~350 lines of `!important` CSS — those exist only to fight Streamlit/BaseWeb dark-mode defaults; a typed Flet theme deletes that entire failure class.
- **Validate the full M3 ColorScheme mapping on a real screen** — `primary`/`secondary`/`tertiary`/`surface` roles are not 1:1 with the brand palette. **Light-only** (one `theme_mode` line); dark mode deferred (YAGNI).

## Entry-point wiring (the dual-mode contract — DO NOT break)
- `src/main.py` `__main__`: `if len(sys.argv) == 1: <Flet UI> else: <CLI>`. **Only the UI branch changes** (Streamlit launcher → `src/ui_flet/launcher.py`). The CLI path, `run_pipeline`, all flags, and **exit codes 0/1/2/3 stay byte-identical** — the nightly scheduled task calls the CLI, never the UI. `ft.run` blocks until the window closes, so the existing `sys.exit(0)` after the UI call still holds.
- New Flet code lives additively in **`src/ui_flet/`**; the Streamlit `src/ui/` stays intact as the rollback floor until **CUT-1**.

## Reference
- **Working bake-off prototype:** [`docs/reference/flet-prototype-spike/app.py`](reference/flet-prototype-spike/app.py) (+ `NOTES.md`, `RUN.md`, `assets/`) — a proven Home + Convert in Flet 1.0 showing the shell, `ft.Theme` brand mapping, `ft.FilePicker`, the async-run-on-thread pattern, and the clean-close lifecycle. **Throwaway reference — delete after PLAT-1.**
- Run it (from the prototype dir, with a Flet 1.0 venv): `python app.py` (native) · `SPIKE_WEB=1 python app.py` (web on :8701).
- **Docs (version-matched):** https://flet.dev/docs/getting-started/ · https://flet.dev/docs/controls/ · https://flet.dev/docs/reference/  — **NOT** old `0.2x` blog tutorials.
