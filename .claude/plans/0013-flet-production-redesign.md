# 0013 — Flet 1.0 production redesign (program plan)

- **Status:** PLAN complete + adversarially reviewed — **awaiting user approval + answers to the open decisions below.** No implementation yet.
- **Supersedes:** [Plan 0012](0012-ui-ux-discovery-and-redesign.md)'s pywebview-repackage recommendation. Tech is LOCKED (see [DECISIONS 2026-06-29](../docs/claugentic-DECISIONS.md)): native **Flet 1.0**, ETL/CLI core reused unchanged.
- **Review:** YAGNI = `OVER-BUILT` (trims applied below) · Plan-gate = `CHANGES_REQUIRED` (all 5 required changes incorporated below).

## Scope locked (2026-06-29 — user decisions applied)

These decisions re-scope the program (and converge with the YAGNI review). **The slice table below is the full reference; where it conflicts with this block, this block wins.**

- **Mapping Editor:** ship **IA-8a (select a pre-built config) only**. **IA-8b (full column-mapping editor) → DEFERRED to ROADMAP** (part of the config-capability epic below). The user authors district configs for now; the editor is a future capability.
- **Production extras (OPS-1..6): DEFERRED to ROADMAP** — discussed *after* the config-editor work. This program = the Flet UI port + foundations + signing + cutover only. (Aligns with the YAGNI review.)
- **Build targets:** **keep Windows + Linux + macOS** — PLAT-3 ports windowed Flet packaging to all three release jobs.
- **Code-signing (PLAT-4): SignPath Foundation (FREE)** — the repo is public/OSS (`myblueprint-spaces/DistrictSync`), so it qualifies. No longer cost-gated. See note below.
- **Help (IA-7):** the release "Help/docs" URL now points to the **org knowledge-article base**, so the bundled `docs/` + in-app Help may be redundant — IA-7 is kept **minimal/flexible** (likely an org-KB link rather than rendering bundled markdown); finalized with the docs-strategy decision in the deferred epic.
- **Net program:** ~16 slices — PLAT-0/1, DS-1/2, PLAT-2/3/4, IA-1..7, IA-8a, IA-9, CUT-1.

### Code-signing reality (researched 2026-06-29)
- **Instant SmartScreen-clear requires an EV cert (paid, ~$300–600/yr + HSM) — there is NO free/cheap instant option.** All free/cheap (OV-class) certs build SmartScreen reputation gradually over download history.
- **Chosen (free): [SignPath Foundation](https://signpath.org/)** — free OV signing for qualifying OSS via CI (GitHub Actions); cert from Sectigo; key on their HSM. Caveats: publisher shows **"SignPath Foundation"** (not myBlueprint/SpacesEDU); does **not** instantly clear SmartScreen (reputation builds with downloads).
- **Cheap-paid upgrade (optional, later): [Azure Artifact Signing](https://azure.microsoft.com/en-us/pricing/details/artifact-signing/) (~$10/mo)** — shows *your* verified org name; same reputation-building behavior; needs a paid Azure subscription.
- **Plan:** start free with SignPath Foundation; document the "More info → Run anyway" step in the partner quick-start; revisit Azure/EV only if SmartScreen friction is real for partners.

### Deferred body of work (→ ROADMAP) — config-capability epic
A future epic, **after this Flet UI + a dedicated UI/UX layout pass**: (1) catalog the config-mapping **column capabilities** (every transform/mutation we support); (2) author **workflow documentation** depicting how each field is realized + the config per field, **with Excalidraw diagrams**; (3) decide how to **illustrate/facilitate** setting these via UI/UX, then **formalize + standardize** the capabilities and their usability; (4) reconcile/remove the now-redundant bundled `docs/` (release URL now → org KB); (5) design + build the **full config editor/creator** (IA-8b). The production-extras (OPS) discussion follows this.

## Vision

Re-implement DistrictSync's **presentation layer only** as a single **native Flet 1.0 desktop app** that calls the unchanged, tested ETL/CLI core (`src/etl`, `src/config`, `src/sftp`, `src/scheduler`, `src/main.py` CLI branch — 640 tests, 80% gate) **in-process on a worker thread**. The Flet app owns a real OS window, so **native window-close = clean shutdown** — deleting the entire Streamlit-in-browser compensating stack (idle watchdog, beacon, zombie-websocket reaping, "close this tab" page) and the ~15s blank-tab cold start. Product truth: the real engine is the invisible nightly **scheduled CLI** sync; the UI is its cockpit for a non-technical admin who opens it 2–3×/year, whose deep job is **trust** — *"I want to stop worrying that the roster synced."* Every surface is **verdict-first** (plain-language green/amber/red before any metrics), **humanized** (friendly district name, plain timestamps, no raw config ids / filenames / stack traces), and ships as **one windowed (no-console) signed exe** that bundles the Flutter client offline.

Architecture decision recorded in full in [DECISIONS](../docs/claugentic-DECISIONS.md) (2026-06-29).

## ⚠️ Grounding caveat (honest)

The plan-development agents could **not read the working prototype** — the `prototype`/`spec` args arrived as `undefined` (a tool-threading glitch), so the plan is grounded in canonical Flet 1.0 API + the verified repo + the **empirical findings I gathered this session**. The prototype **does exist** (`scratchpad/spike/flet_app/`) and **I empirically verified** the load-bearing claims myself this session: native window-close → process tree gone in ~0.5s with **zero orphans**; `flet pack` bundles the Flutter client (offline-capable); no console when windowed; the flet-desktop+flet-web requirement; FilePicker-as-async-service. So PLAT-0 (below) mainly **formalizes the version pin + offline pre-seed**, not the lifecycle (already proven). PLAT-1's conventions doc must harvest the prototype's `NOTES.md`.

## Design foundations (Flet-native)

Three-tier tokens in pure Python → **one `ft.Theme`**. PRIMITIVE = brand values harvested 1:1 from `src/ui/brand.py` (`#1D5BB5/#0F2D6B/#0EA5E9/#16A34A/#F0F6FF/#DBEAFE/#0F172A/#64748B`) — port the **values, not** brand.py's ~350 lines of defensive `!important` CSS (that failure class disappears with a typed theme). SEMANTIC = intent aliases (`color.status.healthy/warning/failed`, `color.action.primary`, …) + type/spacing/radius scales. COMPONENT = token-referencing controls. `build_theme()` maps semantics into a Material-3 `ColorScheme`. **Accessibility is a token/component guarantee** (every fg/bg pair contrast-checked ≥4.5:1 at authoring time — the old Exit button failed this; factories set focusable + visible focus + min target; verdict never color-alone). Honestly deferred (localhost, 2–3×/yr, Flutter canvas = no DOM): full screen-reader certification, axe/Lighthouse gates, dark mode (light-only) — ROADMAP unless a district WCAG mandate surfaces.

## IA model

State-aware grouped `NavigationRail` driven off `AppConfig.is_complete()`/`schedule_registered`: **Get started** (Setup, prominent when unconfigured) · **Everyday** (Home/Status, Run History, Convert) · **Advanced** (Mapping Editor, Help, Check My Setup). **Home branches THREE ways:** (a) unconfigured → onboarding hero + Start Setup; (b) configured + healthy → green verdict + metric tiles; (c) **configured-but-broken** (the state Streamlit's bare try/except hid) → explicit amber/red "needs attention" naming the fault + a fix path. Status reads **degrade gracefully** to a calm "status unavailable" — the cockpit must never crash. The decouple-the-sync reassurance ("closing this window does not stop the nightly Windows-scheduled sync") appears at every leave point.

## Phased slices (revised post-review)

Effort S/M/L/XL · risk low/med/high. **Bold = review-mandated change.**

| ID | Phase | Slice | Eff/Risk | Deps |
|---|---|---|---|---|
| **PLAT-0** | 0 Foundation | **Throwaway spike: resolve exact `flet`/`flet-desktop` version pin + offline Flutter-client pre-seed + re-confirm zero-orphan close + bundle size on a real Win11 target** (gate-required) | M/med | — |
| PLAT-1 | 0 Foundation | Productionized Flet shell + lifecycle + worker→UI marshalling + `ft.FilePicker` wrapper + **early-failure error path (no-console build must not die silently)** + the (now-authoritative) `FLET_CONVENTIONS.md` + pinned deps + `app_version()` helper + windowed-exe smoke; strike ROADMAP **UX-01/03**/05/19 | XL/high | PLAT-0 |
| DS-1 | 1 Design system | Token module + `ft.Theme` + ErrorCard + **HealthVerdictBanner (pulled forward)** + button/Card primitives + 4 state patterns; **verdict-mapping logic extracted to a coverage-counted pure module + tests** | L/med | PLAT-1 |
| DS-2 | 1 Design system | Verdict variants + friendly-`district_name` helper (**pure, tested**) | S/low | DS-1 |
| ~~DS-3~~ | — | **DISSOLVED per YAGNI** — DataTable→IA-6, FileChip→IA-5, StepProgress→IA-4; promote to shared on 2nd use | — | — |
| PLAT-2 | 2 Hardening | Replace tkinter folder picker with `ft.FilePicker` everywhere | M/med | PLAT-1 |
| PLAT-3 | 2 Hardening | Rewrite `release.yml` build for the **windowed (no-console)** Flet exe; retire the console build | M/med | PLAT-2 |
| PLAT-4 | 2 Hardening | Authenticode code-signing (cert-gated; unsigned PLAT-3 exe is the shipping floor) | M/med | PLAT-3 |
| IA-1 | 3 Surfaces | Design-system→shell assembly + state-aware grouped NavigationRail (live) | M/low | DS-1,2 |
| IA-2 | 3 Surfaces | First-run onboarding + decouple-the-sync reassurance | M/low | IA-1 |
| IA-3 | 3 Surfaces | Home → three-way health dashboard; **status-derivation + graceful-degradation in a pure tested module** | L/med | IA-1,2 |
| IA-4 | 3 Surfaces | **Setup Wizard port (HIGHEST-CARE)** — calls `register_task`/`SFTPUploader` UNCHANGED; **relocate `_classify_schedule_error` + its test out of the Streamlit page into a presentation-neutral module** (it is UI-layer code today, NOT core); architect core-untouched gate | XL/high | IA-1 |
| IA-5 | 3 Surfaces | Convert port → real `run_transform`/`DataLoader` + `write_guard`; human verdict; SFTP pre-flight confirm + >20% anomaly ack; FileChip built here | L/med | IA-1,4 |
| IA-6 | 3 Surfaces | Run History port → verdict + staleness banner (**staleness pure + tested**); DataTable built here | M/low | IA-1,3 |
| IA-7 | 3 Surfaces | Help port → render `docs/` markdown natively (`ft.Markdown`) + bundle-path resolution | M/low | IA-1 |
| **IA-8a** | 3 Surfaces | **Mapping Editor: select-a-pre-built-config path (unconditional)** | S/M, low | IA-1 |
| **IA-8b** | 3 Surfaces | **Full 7-step column-mapping editor port + confirm-before-overwrite + backup + diff — CONDITIONAL on the persona decision; deferred to ROADMAP if "select-only"** | XL/high | IA-8a |
| OPS-1 | 4 Extras | Pure `run_setup_diagnostics()` engine (`app_version()` already pulled into PLAT-1) | L/med | — |
| OPS-2 | 4 Extras | `--check-setup` CLI flag over the engine | S/low | OPS-1 |
| OPS-3 | 4 Extras | Support diagnostics bundle (PII/secret-safe: allowlist + re-derived scrubbed config + no keyring read; **+ log-tail CONTENT redaction + planted-secret absence test**) | M/med | OPS-1 |
| OPS-4 | 4 Extras | Privacy-respecting update check + manual-update doc | M/low | OPS-1 |
| OPS-5 | 4 Extras | Flet "Check My Setup" screen + Export Diagnostics + update line (**coverage-omit only the thin view code**) | L/med | IA-1,OPS-1,3,4 |
| OPS-6 | 4 Extras | Partner-readiness: first-run quick-start + prominent support entry | S/low | IA-2,OPS-5 |
| IA-9 | 5 Cutover | Cross-surface humanization (**pure helpers + tests**) + persist `docs/claugentic-PRODUCT.md` | M/low | IA-3,4,5,6 |
| CUT-1 | 5 Cutover | **Cutover: Flet becomes the only UI; remove Streamlit deps + `src/ui`. Migrate-before-delete checklist names `_classify_schedule_error`, the run-history parser, brand values.** Real-server exe verification | M/med | all IA + IA-9 |

**Recommended (non-blocking, from gate):** a tiny CI assertion that `flet`/`flet-desktop` are **exact-pinned** and match the version stated in `FLET_CONVENTIONS.md` — converts the API-drift safeguard from doc-only to gate-enforced.

## Sequencing

Foundation → design system → surfaces → extras → cutover. **PLAT-0 (spike) gates PLAT-1**; PLAT-1 gates everything. DS-1 next (every screen consumes it; HealthVerdictBanner is in DS-1 so the verdict-first spine exists from the start). DS-2 + the PLAT hardening slices (PLAT-2/3/4) + the OPS engine track (OPS-1→2/3/4) run **in parallel** off their roots. IA-1 bridges design-system→shell and gates the surface ports, which then parallelize (IA-2/4/7/8a concurrent; IA-3 after IA-2; IA-5 after IA-4; IA-6 after IA-3). **IA-4 (Setup Wizard) gets the most scrutiny** (security-sensitive scheduler/keyring/elevation logic — called unchanged, pure tests ported, architect core-untouched gate before it lands). IA-9 sweeps the landed surfaces. **CUT-1 is last and is the rollback floor** — it cannot start until every surface + IA-9 are green and the windowed exe is verified on a real server.

## Cutover & rollback

The **CLI and the nightly scheduled CLI path are never touched** — `src/main.py`'s argv-present branch + `run_pipeline` + flags + exit codes 0/1/2/3 stay byte-identical; the Task Scheduler entry calls the same CLI. Only the argv-absent UI branch swaps (Streamlit launcher → Flet launcher) in PLAT-1. **Streamlit stays the live rollback shell until the last surface ports** — `src/ui/` + the `streamlit` dep are removed only in CUT-1, after a real-server exe verification. Per-slice: each lands on an isolated branch behind all gates (pytest + SD74 snapshot + tree-check + ruff/mypy(non-UI)/bandit + config validation) with an architect core-untouched check; a failed slice degrades to "Flet surface not yet ready," not a broken product. PLAT-4 independently revertible (unsigned PLAT-3 ships). Reverting the CUT-1 commit restores Streamlit wholesale.

## Open decisions (need your input)

**Plan-shaping (asked separately):**
1. **Mapping Editor persona** — select-a-config only (IA-8a, recommended) vs build the full editor (IA-8b) now? *Default: select-only; defer the full editor to ROADMAP.*
2. **Production extras (OPS-*) scope** — YAGNI flags them as net-new feature work for a "re-port." You explicitly requested them. Keep in this program (Phase 4) vs ship the port first then OPS as a fast-follow? *(Code-signing PLAT-4 stays regardless — you asked for it and the gate agrees it's warranted.)*
3. **Code-signing cert** — OV (ship now, brief SmartScreen reputation warnings) vs EV (clears SmartScreen immediately; higher cost + lead-time).
4. **Linux/macOS builds** — keep (port to the windowed Flet pattern) or Windows-district-server-only (drop the build-linux/build-macos CI jobs)?

**Proceeding on recommended defaults unless you object:** exact pin = resolve in PLAT-0 (likely `flet==0.85.3`); light-only theme (YAGNI for a single-user desktop tool); staleness threshold = `schedule_time + ~26h` grace; disk-space floors set from a representative GDE/output size in PLAT-0; `--check-setup` on-demand + offered as the Setup Wizard final step; single-instance guard **deferred** (YAGNI); GitHub repo public assumption for OPS-4 update-check (drop/replace if private); no WCAG-AA procurement mandate assumed (a11y baseline only).

## Key risks (full list in the workflow output)

- **IA-4 Setup Wizard port** = biggest regression risk (security-sensitive scheduler/keyring/elevation/password logic) → call modules unchanged, port pure tests, architect gate, never log/persist/argv the password.
- **Flet 1.0 beta API drift** → the read-first `FLET_CONVENTIONS.md` (each rule paired with a "NOT the 0.2x way" counter-example) + exact pin + the CI pin-assertion.
- **No-console build hides boot errors** → PLAT-1 early-failure path (write to log + error dialog before the shell mounts), verified in the smoke.
- **Worker→UI marshalling** = the top Flet 1.0 correctness trap → nailed in PLAT-1, codified in the conventions doc, reused everywhere.
- **PII/secret leak in the support bundle** → allowlist + re-derived scrubbed config + no keyring read + log-tail content redaction + planted-secret tests.
- **New UI layer has no coverage** if the whole package is omitted → extract trust-critical pure logic (verdict, status-derivation, staleness, humanization, worker/picker contracts) into coverage-counted modules; omit only thin view code.

## Next step (gate)

Approve the plan + answer the four plan-shaping decisions, then I start **PLAT-0** (the throwaway spike: resolve the version pin + offline pre-seed + re-confirm the windowed-exe lifecycle on a real target) → its result makes the `FLET_CONVENTIONS.md` authoritative → **PLAT-1**. Each slice goes plan→spec→build→verify behind the harness gates before the next.
