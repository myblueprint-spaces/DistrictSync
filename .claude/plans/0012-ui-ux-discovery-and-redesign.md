# 0012 — UI/UX discovery & redesign direction

- **Status:** DISCOVERY complete — **awaiting user direction approval** (no implementation yet). This is the `discuss → plan` artifact; an implementation spec follows only after the direction is approved.
- **Recommendation:** **`partial_redo`** (two-stage: ship in-browser shutdown fixes now → repackage Streamlit in a native window, gated by a spike) carrying a layer of shell-agnostic content/IA/visual fixes.
- **References:** `src/ui/` (all surfaces), `src/ui/lifecycle.py` + `.claude/plans/0011-graceful-ui-shutdown.md` (review target was branch `feat/graceful-ui-shutdown`).
- **Method:** product-designer heuristic review of all 8 surfaces (8 agents) + architecture-options analysis + YAGNI counterweight + synthesis (11 agents total), **plus a live runtime check** (real Streamlit server driven headless): cold-start timing, shutdown/watchdog behavior, Exit/orphaned-tab behavior, and per-surface screenshots.
- **Audience reminder:** non-technical school-district IT/admin staff; portable PyInstaller `.exe` on a Windows server; UI used ~2–3×/year (setup once, occasional ad-hoc convert, glance at history). The product owner's framing: *"no thought has been put into UI/UX — built out of utility, just a wizard and an ad-hoc run screen."* So this is a UI that was never intentionally designed.

---

## Live evidence (verified this session, not inferred)

1. **Cold start ≈ 15s with no feedback → "looks hung."** Measured 14.99s spawn→ready on a *warm source checkout*; worse from the frozen `.exe` on a cold server. The launcher opens the browser before Streamlit is ready, so first paint is a blank/erroring tab — the product's first impression.
2. **The idle watchdog is NOT fundamentally broken.** On a **clean** tab-close, `num_active_sessions()` dropped `1→0`, the idle clock started, and the server `os._exit(0)`'d cleanly after grace. The real-world "never reaps" is most likely (a) the **90s silent grace** reading as a hang and/or (b) an **abrupt window-close zombie websocket** lingering past patience/ping-timeout. The fix is *immediate disconnect detection + visible feedback*, not "repair the count."
3. **The Exit button leaves an orphaned, frozen tab.** Confirmed live: Exit kills the server (exit 0) but the intended goodbye **never paints** (it `os._exit`s mid-rerun), so the user is left on a greyed-out frozen page with no "you can close this" closure. Browsers can't self-close the tab.
4. **Visually "default Streamlit with a brand band painted on":** heavy dark sidebar, dead right-side whitespace, washed-out low-contrast Exit button, **truncated** manage-view metrics (`sd54…`/`Not a…`/`Disab…`), raw config ids (`sd40myedbc`) and raw filenames shown to users, Home's "Navigation" column duplicating the sidebar.

**Review coverage:** 8 surfaces, **67 findings** (4 critical, 19 high, 27 medium, 11 low). Full per-surface findings: `scratchpad/discover_full.json` (session artifact).

---

## Recommendation: `partial_redo` (two-stage)

The pain is **two-rooted**, and the two roots decide the architecture; everything else is content/IA debt that must happen regardless of shell.

- **Root 1 — Shutdown/orphaned-tab.** Exists *only* because the UI lives in a browser tab the app doesn't own (no clean last-disconnect signal; a tab can't self-close). In-browser fixes have a permanent ceiling ("you can close this tab" + a watchdog/beacon you maintain forever).
- **Root 2 — Cold-start perception.** ~15s blank tab because the browser opens before Streamlit is ready.

**The proportionate move:** keep 100% of the Streamlit pages + brand work + ETL/CLI core, and change only the **launch/host layer** — host the *unchanged* Streamlit app inside a **chrome-less native window via pywebview** (OS-native WebView2, preinstalled on the Win11 target). OS window-close becomes the real shutdown signal, which **deletes the entire compensating stack** (idle watchdog, beacon, zombie-websocket handling, single-instance guard, "close this tab" page) and fixes cold-start *perception* (app-owned window + splash from frame 0). Still one PyInstaller exe.

### Why not the other two

| Option | Framework | Fixes shutdown root? | Fixes cold-start? | Effort / Risk | Verdict |
|---|---|---|---|---|---|
| **improve_in_place** | Stay Streamlit-in-browser | ❌ ceiling = "close this tab" page + watchdog forever | ❌ perception only | L / low | Can never fix shutdown at the root |
| **partial_redo ✅** | Streamlit hosted in pywebview native window | ✅ window-close = signal; whole stack deleted | ✅ perception (Streamlit still boots underneath) | M / **medium** (spike-gated) | **Recommended** — keeps all pages + exe; one host-layer change |
| **full_redesign** | Native rewrite (PySide/Tauri) drop Streamlit | ✅ fully native lifecycle | ✅ truly (no Streamlit boot) | XL / high | Gold-plating for a 2–3×/year tool; re-implements 5 working pages |

**Honest sequencing caveat (the key nuance):** `partial_redo` is the *destination*, but we do **not** bundle the pywebview move with a 7-surface redesign or onto the shutdown branch. We ship the in-browser graceful-shutdown fixes **now** (proportionate, low-risk), deliver the shell-agnostic content fixes in parallel, and **gate the pywebview repackage behind a packaging spike**. The YAGNI counterweight's core warning stands: *don't build a cathedral on a doorknob* — scope each slice to its charter.

---

## Shutdown lifecycle redesign (detailed)

Make shutdown a **two-phase, always-visible transition**, and **decouple "close this window" from "the nightly sync"** everywhere the user might leave.

- **Phase 1 (paints first, synchronous):** on Exit / Wizard Finish, immediately rerun into a dedicated full-page *"Closing DistrictSync… one moment."* state (hide nav, brief reassuring line, bounded indicator). **The process must outlive the paint of its own goodbye.**
- **Phase 2 (deferred):** stop the server only **after** that frame flushes — schedule the stop on a short (~0.8–1.5s) timer / background thread, **never inline** in the same rerun.
- **Phase 3 (terminal page):** the last thing the server paints is an on-brand **"DistrictSync is closed."** page:
  - *"You can safely close this browser tab."*
  - **(load-bearing)** *"Your nightly automatic sync is NOT affected — it runs on a Windows scheduled task, not in this window, and will continue on schedule."*
  - *"To use DistrictSync again, reopen it from your desktop or Start menu."*
- **Orphaned-tab reality:** a tab the app opened cannot script-close itself — accept it honestly; the terminal page is the best achievable in-browser end-state, which is *exactly why* the pywebview repackage (OS window-close ends everything cleanly) is the real root fix.
- **Abrupt close (the common case):** set Streamlit `server.websocketPingTimeout` small so the zombie reaps in seconds instead of 90s. Try the config knob **alone** first; add a `beforeunload`/`visibilitychange` beacon **only if** reaping is still too slow (don't pre-build beacon infra for a single-user localhost app).
- **Wizard Step 5 (highest-stakes moment):** after "Schedule registered," add a primary **"Finish & Close DistrictSync"** routing into the same flow, paired with: *"You can close DistrictSync now. Your daily sync runs automatically through Windows Task Scheduler at {time} — it does not need this window open."*
- Keep **one shared close function** for Exit + Wizard Finish (a function, not a "canonical shutdown surface" framework — there are two call sites).
- **Defer to ROADMAP:** aria-live/WCAG-AA conformance on the goodbye page (ship readable, on-brand, ≥16px copy now); the single-instance self-explaining banner.

## Cold-start redesign

Separable from shutdown — its own small slice (or folded into the pywebview spike). In-browser fix: **stop opening the browser before Streamlit is ready** — either (a) defer `webbrowser.open` until a readiness probe against `:8501` passes, or (b) open an immediate static holding page (*"DistrictSync is starting — this can take up to ~20 seconds"*) that auto-redirects when ready. Make relaunch idempotent. Keep splash copy calm and **bounded** ("up to ~20 seconds"), never a bare spinner.

---

## Prioritized backlog (impact × effort)

P0 = genuinely painful · P1 = high-impact, shell-agnostic · P2 = important, contained · P3 = strategic / decision-dependent.

| ID | Title | Tier | Impact | Effort | Surfaces |
|---|---|---|---|---|---|
| UX-01 | Paint-before-die Exit + static "you can close this tab" terminal page | P0 | high | M | shutdown, launcher, global Exit |
| UX-02 | Decouple-the-sync reassurance copy at Wizard Finish + closed page | P0 | high | **S** | Wizard Step 5, terminal page |
| UX-03 | Reap abrupt window-close reliably (websocket ping timeout) | P0 | high | **S** | launcher, Streamlit config |
| UX-04 | Cold-start: readiness probe / splash before first paint | P0 | high | M | launcher |
| UX-05 | Spike + repackage Streamlit in a chrome-less pywebview window | P1 | high | L | launcher, build, folder_picker |
| UX-06 | Home: branch into onboarding vs health dashboard | P1 | high | L | Home |
| UX-07 | Friendly `district_name` everywhere instead of raw config keys | P1 | high | **S** | Home, Convert, Wizard manage |
| UX-08 | Help page: MkDocs-syntax render shim (correctness, not cosmetics) | P1 | high | M | Help, docs/partner |
| UX-09 | Convert: data-quality report as a human verdict | P1 | high | M | Convert, quality/report |
| UX-10 | Convert: SFTP pre-flight confirmation + anomaly acknowledgement | P1 | high | M | Convert |
| UX-11 | Run History: lead with a verdict + staleness banner | P1 | high | M | Run History |
| UX-12 | Wizard: make the Windows-password step foolproof | P1 | high | M | Wizard |
| UX-13 | Wizard: dry-run result as a friendly summary, not a stdout dump | P2 | high | M | Wizard |
| UX-14 | Wizard manage-view: fix truncating status metrics | P2 | medium | **S** | Wizard |
| UX-15 | Humanize error paths + add loading spinners across the app | P2 | medium | M | Wizard, Convert, History, Mapping |
| UX-16 | Mapping Editor: confirm-before-overwrite + backup + diff (data-loss risk) | P2 | high | M | Mapping Editor |
| UX-17 | Mapping Editor: resolve the persona (Advanced/Partner gating) | P3 | medium | XL | Mapping Editor, IA |
| UX-18 | Intentional IA: grouped, state-aware navigation (`st.navigation`/`st.Page`) | P3 | medium | M | global nav, Home |
| UX-19 | Move base theme to `.streamlit/config.toml`; shrink `brand.py` | P3 | medium | L | brand.py, config.toml |

**Quick wins (high impact, low effort — do first):** UX-02, UX-07, UX-03, UX-14, and the "drop the Dev tab + guard the file read" slice of UX-08.

## Sequencing

- **Phase 0 — shutdown charter (now, the `feat/graceful-ui-shutdown` branch):** UX-01, UX-02, UX-03. The genuinely painful, in-scope items; small, low-risk; resolve the frozen-tab crash-perception and the first-run "did I break the sync?" fear. **Do not bundle the redesign onto a shutdown branch.**
- **Phase 1 — cold-start (separate small slice):** UX-04 (or fold into the Phase 3 spike).
- **Phase 2 — shell-agnostic quick wins + content fixes (parallelizable, not blocked by the architecture decision):** UX-07, UX-14, then UX-08, UX-09, UX-11, UX-12, UX-06.
- **Phase 3 — architecture spike + repackage:** UX-05 (pywebview). If the spike is green, it retires Phase-0's in-browser shutdown scaffolding and fixes cold-start at the root.
- **Phase 4 — larger / decision-dependent:** UX-10, UX-13, UX-15, UX-16, then the strategic UX-17/18/19 once the Mapping Editor persona is decided.

## Open questions / gaps (need owner input before some slices)

1. **Mapping Editor persona** — partner/integration staff vs everyday admin? Drives UX-17/18 (gate it as "Advanced/Partner setup"?). Product-scope call.
2. **pywebview / WebView2 viability** — validate via spike: PyInstaller+pywebview+WebView2 bundling, `folder_picker.py` tkinter coexistence/replacement, theming/dark-mode inside the embedded webview, fallback for older/locked-down servers.
3. **Real `.exe` cold-start time** on a representative district server (the ~15s is from source; splash copy depends on truth).
4. **Is a non-browser (embedded-webview) distribution acceptable** to districts that whitelist the system browser?
5. **Does the websocket-timeout config alone reap the zombie**, or is the beacon actually needed? Validate empirically.
6. **Ad-hoc Convert frequency vs the scheduled run** — gauges how much UX-09/10 investment is warranted vs the monitoring surfaces.
7. **Any accessibility (WCAG-AA) procurement mandate?** If none, goodbye-page/AA work stays deferred; if one exists, it re-prioritizes several P3 items.

## Caveats on this review

- The **YAGNI counterweight agent mis-grounded on `main`** (claimed "no lifecycle code exists in the worktree") — the arch-options and synthesis agents correctly read the feat-branch `lifecycle.py:229-275`. The YAGNI *scoping logic* (cut the cathedral, scope each slice) still holds; its "no code" premise was wrong.
- This is a **same-model review** (judge and builder share a family) — treat the convergence of arch+synth on `partial_redo` as a reduction of single-pass bias, not a cross-model guarantee.
- The recommendation is a **direction**, not yet an implementation spec. Per `docs/claugentic-WORKFLOW.md`, the next gate is user approval of the direction, then a spec for Phase 0.

## Next step (gate)

Awaiting your decision: confirm `partial_redo` + the phased sequencing (and answer the open questions you can), and I'll draft the **Phase 0 implementation spec** (UX-01/02/03) for approval before any code lands.
