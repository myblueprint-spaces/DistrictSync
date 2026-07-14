# 0011 — Graceful UI shutdown (no perpetual console)

- **Status:** Spec'd (review CHANGES REQUIRED addressed) — awaiting user approval
- **References:** `src/ui/launcher.py` · `src/ui/Home.py` · `src/ui/pages/01_Setup_Wizard.py`

## Problem

[src/ui/launcher.py:59](src/ui/launcher.py:59) ends with `sys.exit(stcli.main())`, which blocks forever — the Streamlit **server keeps running after the browser tab is closed** (Streamlit has no built-in "exit when the last client disconnects"). On an unattended district server the console/process leaks indefinitely until someone notices, and it causes a second symptom: the next launch finds **port 8501 already in use**, so re-opening the app can fail or spawn a duplicate server.

Scope locked with user: **graceful exit only**; portable single-exe distribution stays (no installer).

## Goals / Non-goals

- **Goal:** Closing the browser → the server + console exit on their own shortly after (within a short grace period).
- **Goal:** An explicit **Exit** control for immediate, intentional shutdown — and a "Setup complete, you can close this window" + **Finish & Close** at the end of the Setup Wizard (the moment the user named).
- **Goal:** A **single-instance guard** so a still-running/duplicate server can't block relaunch — a second launch opens a browser to the existing instance and exits (no second server, no port-8501 clash).
- **Non-goal:** Installer / fixed install path / onedir / startup splash / broader UX (explicitly deferred per user).
- **Non-goal:** Touching the CLI or the scheduled-task path — scheduled syncs run the **CLI**, not the UI, so UI shutdown can't affect them.

## Approach

New `src/ui/lifecycle.py` concentrates the lifecycle logic (keeps `launcher.py`/`Home.py` thin):

- **`should_exit(active_count, ever_connected, idle_since, now, grace) -> tuple[bool, float | None]`** — a **pure** decision function (the only unit-testable part): never exit before the first browser connects (startup window has 0 sessions); once `ever_connected`, start the idle clock when sessions hit 0; exit only after `grace` seconds continuously idle; reconnect clears the clock.
- **`start_idle_watchdog(grace=90, poll=5)`** — a **daemon thread**, started **once per server process** (via `@st.cache_resource` singleton in `Home.py`). Each tick reads the active browser-session count via `streamlit.runtime.exists()` → `get_instance()._session_mgr.num_active_sessions()` (the minimal-surface accessor; note this reaches the **private `_session_mgr`** attribute — named explicitly in ONE place so a future Streamlit rename fails loudly there), feeds `should_exit`, and `os._exit(0)`s **only when `should_exit` is True AND no write is in flight** (see `write_guard`). Wrapped in try/except so any Streamlit-internal API change **degrades to a no-op + logged warning, never crashes the UI** (the Exit button remains the always-works fallback).
- **`write_guard()` / `safe_to_exit()`** — a context manager over a module-level counter+lock marking a critical write in progress; `safe_to_exit()` is False while count > 0. `02_Convert.py` wraps its whole conversion (`DataLoader.save_all` + any SFTP upload) in it. Both the watchdog and `request_exit()` refuse to `os._exit` while a write is in flight — the watchdog defers to the next tick; `request_exit()` shows "Finishing your conversion…" and waits (bounded, ~30s) for `safe_to_exit()` before exiting. This closes the truncation risk (below).
- **`request_exit()`** — wait for `safe_to_exit()` (bounded), render a goodbye line, then `os._exit(0)`. Used by the Exit / Finish & Close buttons.
- **`already_running(port=8501)`** — in `launcher.py`, before starting Streamlit, GET `http://localhost:8501/_stcore/health` with a short timeout; treat as "already running" **only if the response body is exactly `ok`** (not mere connectivity/200 — a non-Streamlit 8501 occupant won't match). If so, open a browser to the existing instance via `webbrowser.open` and exit(0) (no second server). It may surface a new tab rather than focus the existing one — accepted. **Startup-vs-shutdown race:** if the old server's watchdog is `os._exit`-ing at that instant, the opened tab may hit a just-dead port; worst case the user reloads. Bounded and documented, not silently ignored.

Semantics chosen deliberately: exit keys off **browser disconnect** (websocket gone), not inactivity — an open-but-idle tab keeps the app alive; only *closing* it triggers shutdown. `grace` (~90s) absorbs refreshes/brief recon­nects and accidental closes.

Why `os._exit(0)` (not `sys.exit`): it promptly kills the blocking Streamlit server thread without waiting on it. **The data-integrity catch (review #1):** `DataLoader.save_all` is atomic only via a `finally` block ([loader.py:87-92](src/etl/loader.py)) that a hard `os._exit` would skip — exiting mid-commit could leave the output dir cross-file-torn + orphan `.tmp_<ts>/`/`.bak_<ts>/` (and `save_all` does NOT currently sweep stale temp dirs, so "next run self-heals" is false). Hence the `write_guard` above: no exit path — watchdog or button — fires while a conversion write is in flight. The keyring/SFTP-credential write (`SFTPUploader.store_password`) is synchronous and safe; the Setup Wizard does not call `save_all`, so its Finish & Close is always safe.

## Affected files

- `src/ui/lifecycle.py` — **new** — `should_exit` (pure), `start_idle_watchdog`, `write_guard`/`safe_to_exit`, `request_exit`, `already_running`. **+ ARCHITECTURE_TREE entry** (src/**/*.py is tree-indexed even though src/ui is mypy/coverage-excluded).
- `src/ui/launcher.py` — single-instance guard before `stcli.main()`.
- `src/ui/Home.py` — start the watchdog once (`@st.cache_resource`); add a sidebar **Exit DistrictSync** control (shared, shows on all pages via the sidebar).
- `src/ui/pages/01_Setup_Wizard.py` — success step: "Setup complete — you can close this window" + **Finish & Close** button → `request_exit()`.
- `src/ui/pages/02_Convert.py` — wrap the conversion run (`save_all` + optional SFTP) in `lifecycle.write_guard()` so an exit can't truncate a write.
- `requirements.txt` / `requirements-dev.txt` / `pyproject.toml` `[ui]`+`[dev]` — pin `streamlit>=1.54,<1.55` (was `>=1.30`): the watchdog depends on a **private** Streamlit internal, so cap the minor to the verified version.
- `tests/test_ui_lifecycle.py` — **new** — unit tests for `should_exit` + `write_guard`/`safe_to_exit` + `already_running`.
- `docs/claugentic-DECISIONS.md` — dated line recording the private-`_session_mgr.num_active_sessions()` dependency, the `/_stcore/health` body==`ok` contract, the streamlit pin, and the `write_guard` rationale (INVARIANTS candidate so a future dependency bump re-verifies it).

## Risks & mitigations

- **`os._exit` truncating an atomic write (data integrity — review #1).** → `write_guard` around the Convert conversion; no exit path fires while a write is in flight (watchdog defers; button waits, bounded). Closes the torn-output / orphaned-temp-dir risk.
- **Streamlit private-API change across versions** (`_session_mgr.num_active_sessions()`). → single guarded access point; failure → watchdog no-ops + logs, UI unaffected; Exit button is the manual fallback. Pin `streamlit>=1.54,<1.55` + a `ui`-marked smoke assertion (below) so a bump that breaks the reach fails in the Playwright suite, not silently in prod.
- **Startup race (0 sessions before browser connects).** → `ever_connected` gate: never exit until ≥1 session has been seen.
- **Single-instance: non-Streamlit 8501 occupant / launch-vs-shutdown race.** → key off health **body == `ok`** (not connectivity); short timeout; worst case (tab to a just-exited port) is a user reload — bounded, documented.
- **`src/ui` is mypy/coverage-excluded** → the testable logic (`should_exit`, `write_guard`/`safe_to_exit`, `already_running`) is **pure** and unit-tested regardless of the coverage omit; the thin Streamlit-coupled glue gets the `ui`-marked smoke assertion + manual smoke.

## Test strategy

- **Unit (`test_ui_lifecycle.py`):** `should_exit` truth table (startup/never-connected, connected, idle-within-grace, idle-past-grace, reconnect); `write_guard`/`safe_to_exit` (False while held, True after); `already_running` against a mocked health response (body `ok` → True; non-`ok` / connection-error → False).
- **`ui`-marked smoke (review #5):** using the existing Playwright `streamlit_server` fixture (`tests/conftest.py`), a **read-only** assertion (no `os._exit`) that `get_instance()._session_mgr.num_active_sessions()` is reachable and returns an int against a real running server — so a Streamlit bump that breaks the private reach fails in the `ui` suite. (`ui` tests are deselected by default; run in the Playwright job.)
- **Manual smoke (recorded in impl — the parts needing a real browser):** (1) launch, close the only tab → process exits ~grace later; (2) click Exit / Finish & Close → immediate exit; (3) double-launch → second opens a browser to the first and exits, no duplicate server / no port error; (4) start a Convert, click Exit mid-write → exit waits for the commit, output dir intact (no `.tmp_`/`.bak_` orphans).
- **Regression:** full `pytest` green; ruff/format clean. (mypy/bandit unaffected — `src/ui` excluded.) SD74 snapshot untouched (UI is outside the ETL path).

## Decomposition (slices)

Single slice — interdependent (watchdog + exit + guard are one coherent lifecycle change), small, all in `src/ui` + one test. Lands complete; no debt.

- [ ] **Slice 1 — Graceful UI shutdown.** `lifecycle.py` + launcher guard + Home watchdog/Exit + wizard Finish&Close + `test_ui_lifecycle.py` + ARCHITECTURE_TREE entry.

## Review

**Reviewer:** `plan-reviewer` (adversarial, clean-context, Opus-4.x) · verified against the repo and the installed Streamlit 1.54.0.

**Verdict:** `CHANGES REQUIRED`

The approach is sound and correctly scoped (lightweight-leaning but the watchdog touches a brittle internal API + a security/PII tool's write path, so the full pipeline + this review is the right call). The single-slice sizing is right. But three claims in the plan are **not true of the repo / of Streamlit 1.54**, one of which is a genuine data-integrity risk, and the internal-API access path is described less precisely (and less safely) than the code will actually need. Fix these in the spec before approval.

### Required changes

1. **`os._exit(0)` CAN truncate a real write — the "no critical cleanup in the UI path" claim (line 31, line 47) is false for the Convert page.** `02_Convert.py`'s conversion routes through `DataLoader.save_all`, which stages into `.tmp_<ts>/`, commits per-file via `os.replace` in `_commit_staged` (`src/etl/loader.py:94`), and **relies on a `finally` block** (`loader.py:87-92`) to rmtree the `.tmp_<ts>/` and `.bak_<ts>/` dirs. `os._exit(0)` skips `finally`, so an exit **mid-commit** leaves the output dir cross-file-torn (new files `1..K`, stale `K+1..N`) **and** orphans `.tmp_<ts>/` + `.bak_<ts>/` in the output dir. The keyring write (`SFTPUploader.store_password`) is synchronous and safe as claimed — but `save_all` is not. The 90s grace does **not** cover this for the **Exit / Finish & Close buttons**, which call `request_exit()` → immediate `os._exit(0)` with no grace; a user can click Exit while a Convert run is mid-write. **Fix the plan:** either (a) gate `request_exit()` and the watchdog tick on an "in-flight write" flag (set around `run_conversion`/`save_all`, e.g. a module-level `threading.Event`) so exit defers until the commit finishes; or (b) explicitly accept and document the torn-output + orphaned-temp-dir risk and confirm the next CLI/UI run self-heals it (note: `save_all` does not currently sweep stale `.tmp_`/`.bak_` from a prior crashed run — verify before relying on self-heal). Do not leave line 31's blanket "no critical cleanup runs in the UI path" unqualified — it reads as proven and it isn't.

2. **State the real internal-API access path and prefer the count accessor — the plan under-describes the brittleness.** Verified on 1.54.0: `streamlit.runtime.exists()` and `get_instance()` exist, but neither `Runtime` nor any public accessor exposes the session list. The actual chain is `Runtime.instance()._session_mgr.list_active_sessions()` — i.e. it traverses the **private `_session_mgr` attribute** *and* a non-abstract internal `SessionManager` protocol method. The plan's "verified present on streamlit 1.54" is true for the method name but hides that this is a **two-hop private/internal reach**, which is the actual version-fragility. Two concrete improvements to fold into the spec: (a) use **`num_active_sessions() -> int`** (also present, `SessionManager`) instead of `len(list_active_sessions())` — it's the minimal-surface accessor and exactly what the watchdog needs; (b) name `_session_mgr` explicitly in the guarded-access block and in the test so a future Streamlit bump that renames it fails loudly in one place. The guarded-degrade-to-no-op design is otherwise sound and is the right call for an internal-API dependency.

3. **Single-instance guard: state the startup race; it is real.** Verified: `/_stcore/health` returns `200 "ok"` as soon as the runtime leaves `INITIAL` (`Runtime.is_ready_for_browser_connection`, wired in `server.py`) — i.e. **before any browser session connects**. Two unaddressed interactions: (a) **launch-vs-shutdown race** — a second launch can see the *old* server's health as `ok` while that server's watchdog is concurrently `os._exit`-ing, so `focus_existing()` opens a browser to a port that dies milliseconds later (no duplicate, but a dead tab). (b) The guard correctly avoids a bare port check (good), but the plan should say what `already_running()` does on a **non-Streamlit** 8501 occupant that returns 200 with a non-`ok` body — confirm it keys off the `ok` body, not just connectivity/status (it must, given the health body is literally `"ok"`). Add a short retry/timeout note for (a).

4. **"Focus the existing instance" overclaims — `webbrowser.open` cannot focus a specific existing tab.** Lines 16 and 27 say a second launch "focuses the existing instance." `webbrowser.open(url, new=0)` opens/raises a browser to the URL; it cannot target or focus the *already-open* Streamlit tab — the user may get a second tab to the same app. Reword to "opens a browser to the existing instance and exits (no second server)" — which is the real, still-valuable behavior. Minor, but the copy should match reality (honesty register).

5. **Tighten the testability claim — automated coverage is available beyond `should_exit`.** The repo already has a Playwright `streamlit_server` session fixture (`tests/conftest.py`, used by `tests/test_ui_smoke.py`) running a real headless server on 8502. `already_running()` is fully unit-testable against a mocked health response (as planned). Keep `should_exit` pure-unit (correct). But the plan should at least *consider* a guarded smoke assertion that a real disconnect drives the active-session count to 0 (the watchdog's actual input), rather than declaring the entire Streamlit-coupled path "manual smoke only" — the `_session_mgr` reach is exactly the part most likely to silently break on a Streamlit bump, and a no-`os._exit` read-only assertion against the running fixture would catch it. Not a blocker; raise the bar from "manual only."

### Sizing / completeness check

- **Slice 1 — OK (single slice is correct).** Watchdog + Exit control + single-instance guard + pure-logic tests + tree entry are interdependent and land vertically complete in one ≤1M session; splitting would create a half-wired lifecycle. **Completeness caveat:** as written it lands with the **#1 data-truncation gap unresolved** (debt) — adding the in-flight-write guard or the explicit documented-acceptance is what makes it land *clean*. With change #1 addressed, the slice is complete and debt-free.

### Harness impact

- **ARCHITECTURE_TREE entry is required — confirmed.** `scripts/claugentic-check_architecture_tree.py` sets `INCLUDE_GLOBS = [":(glob)src/**/*.py", …]`, which presence- **and** staleness-checks `src/ui/lifecycle.py` regardless of the mypy/coverage `src/ui` omit. The plan already lists this (good); the implementer (file author) writes the one-line description, and the `Stop`/`PostToolUse(Write)` hooks will enforce it.
- **Streamlit version pin (DECISIONS, not just a code comment).** This slice takes a hard dependency on a **private** Streamlit internal (`_session_mgr.num_active_sessions()`). The plan defers the pin "to spec" — make it a Stage-9 `DECISIONS.md` line *and* pin/floor `streamlit==1.54.x` in `requirements*.txt`, with the rationale (which private attr, why guarded-degrade is the fallback) recorded so a future `dependency-health` bump knows to re-verify `_session_mgr`/`num_active_sessions`/`/_stcore/health`. This is the load-bearing invariant of the slice — record it (candidate for `docs/claugentic-INVARIANTS.md`).
- **No new STANDARD or agent needed.** Existing `reliability-resilience` + `data-and-persistence` lenses cover the watchdog and the `os._exit`-vs-atomic-write concern; no new dimension.
