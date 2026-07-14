<!-- claugentic-dev-harness@0.3.0 -->
# 0019 — IA-2: first-run onboarding hero + decouple-the-sync reassurance (folds DS-2)

- **Status:** DRAFT → adversarial gate → Spec → build
- **Parent program:** [`0013-flet-production-redesign.md`](0013-flet-production-redesign.md) (IA-2 row: *"First-run onboarding + decouple-the-sync reassurance"*; folds **DS-2** — friendly `district_name` helper) · follows IA-1 (`9588fe9`), DS-1 (`128fa13`), PLAT-2 (`70587ea`)
- **References:** `src/ui_flet/shell.py` (the `_on_leave(page)` leave-point seam + `do_exit`; `build_screens` id-keyed host; the `functools.partial(build_setup, page)` swap pattern — IA-2 swaps the `home` slot the same way) · `src/ui_flet/nav_rail.py` (the rail view — the reassurance line renders here, near Exit) · `src/ui_flet/components.py` (`card`/`primary_button`/`hero_gradient` — build the hero from these) · `src/ui_flet/verdict.py` (`Verdict.WARNING` / `verdict_visuals` — the onboarding verdict is "not set up yet") · `src/config/loader.py` (`available_configs()` + `load_config().district_name` — the DS-2 helper's source, already used by `screens/setup.py`) · `src/config/models.py:283` (`district_name: str = ""`) · `src/config/app_config.py` (`is_complete()` / `schedule_registered` — the unconfigured predicate)

## Problem
The Flet shell renders a **branded placeholder** for every unbuilt surface (`shell.build_placeholder`), including `home`. So a first-run admin who launches DistrictSync — the person whose deep job is *trust* ("did the roster sync?") — lands on a generic "This part of DistrictSync is on its way" card with **no first-run guidance**: no one-line statement of what the tool does, no plain "you're not set up yet" status, and **no path to Setup** other than reading the nav rail. IA-1 correctly *leads* the unconfigured admin to Setup (the rail reorders + selects `setup`), but the moment they click `Home` — or land there once configured-but-unscheduled — there is nothing. The program's IA model (`0013:43`) specifies **Home branch (a): unconfigured → onboarding hero + Start Setup**; IA-2 builds that hero **as a reusable piece IA-3 drops in**, without building IA-3's three-way branching or status-derivation.

Separately, the program mandates a **decouple-the-sync reassurance** — *"closing this window does not stop the nightly Windows-scheduled sync"* — that "appears at every leave point" (`0013:43`). The real engine is the invisible scheduled CLI; a 2–3×/year admin must never fear that closing the cockpit stops the sync. `shell.py`'s `_on_leave` docstring already names IA-2 as the owner of this reassurance. Nothing surfaces it yet.

And the friendly-`district_name` mapping — **DS-2**, a pure tested helper — has no home: `screens/setup.py` inlines the `available_configs()` → `load_config().district_name` lookup (RC2) to populate its dropdown, but there is no reusable `sis_type → human name` function. Onboarding needs it (to greet a chosen district by name, never a raw `sd48myedbc`), and IA-3/5/6 + IA-9 will reuse it. Fold DS-2 into IA-2 as its **first consumer**.

## Goals / Non-goals
- **Goal — first-run onboarding hero (reusable factory):** a calm, branded, **verdict-first** welcome surface for the UNCONFIGURED state. It states what DistrictSync does in one plain line, shows a plain-language "you're not set up yet" status (the `Verdict.WARNING` tone — attention, not alarm), and offers a prominent **"Start setup"** CTA that navigates to the Setup screen via the shell's id-keyed selection. Built as a **factory** (`build_onboarding(...)`) with a `sis_type`-agnostic default and a friendly greeting when a district is already chosen — designed so **IA-3's Home branch (a) drops it in unchanged**. Assembled entirely from `components.py` (cards/buttons) + `verdict`/`tokens` — never hand-rolled controls (the `FilledButton(text=)` trap).
- **Goal — decouple-the-sync reassurance (persistent line near Exit):** the "closing this window does not stop the nightly sync" message surfaced as a **persistent, always-visible reassurance line in the nav rail's trailing area, directly above Exit** (RECOMMENDED — see *Approach*). The `_on_leave` seam stays the documented close-time hook but gains **no behaviour** this slice (the reassurance is ambient, not a close-time interruption). The alternative (confirm-on-exit dialog) is weighed + rejected below.
- **Goal — friendly `district_name` helper (folds DS-2, PURE + COUNTED):** a new pure module `src/ui_flet/humanize.py` exposing `friendly_district_name(sis_type: str) -> str` — maps a SIS id to its human `district_name` (via `loader.available_configs()` / `load_config()`), with a sensible fallback (the raw id) when the config is unknown or fails to load. COUNTED + unit-tested. Onboarding is its first consumer; `screens/setup.py` is **refactored to reuse it** (DRY — kills the inline lookup); IA-3/5/6/9 reuse it later. **Kept minimal** — only `district_name`, not a speculative humanization framework (IA-9 does the broader sweep; YAGNI).
- **Goal — wire onboarding into the shell:** swap the `home` placeholder for the onboarding hero **only in the unconfigured state**, via the same `functools.partial` pattern that swaps `setup` — so the configured-but-unscheduled admin (who leads with Everyday) still sees a placeholder at `home` until IA-3 lands the real dashboard. No IA-3 status logic; a single `AppConfig.is_complete()`-and-`schedule_registered` read (the same predicate `nav._prominent_group` already uses) decides the swap.
- **Non-goal — the Home three-way health dashboard + status-derivation:** that's **IA-3**. IA-2 builds only branch (a)'s *content* (the onboarding hero) as a reusable factory; IA-3 wires the three-way branching (a/b/c), the `status-derivation + graceful-degradation` pure module, and the healthy/broken verdict tiles. IA-2 does **not** read run history, staleness, or task state, and does **not** derive any verdict from live data — the onboarding verdict is a fixed "not set up yet."
- **Non-goal — Setup-step content:** the folders step landed (PLAT-2); scheduler/SFTP/keyring/elevation is **IA-4**. IA-2 only *navigates to* `setup` and reflects `is_complete()`.
- **Non-goal — the ETL/CLI core:** zero change to `src/etl|config|sftp|scheduler|main.py`. Asserted as a gate. (`humanize.py` *reads* `config/loader` but adds nothing to it.)

## Approach
- **`src/ui_flet/humanize.py` (new, PURE, COUNTED):** `friendly_district_name(sis_type: str, *, config_dir: Path | None = None) -> str`.
  - Empty/whitespace `sis_type` → `""` (a caller with no district shows the generic hero copy, no name).
  - Otherwise attempt `load_config(sis_type, config_dir).district_name`; return it stripped **iff** non-empty, else fall back to the raw `sis_type`. Any load failure (`FileNotFoundError`/`ValueError`/unexpected) is caught → fall back to the raw `sis_type` (an admin sees `sd48myedbc` at worst — never a crash, never a blank). Log at `warning` (mirrors `setup._district_options`).
  - `config_dir` passthrough keeps it **testable with a fixture mappings dir** (no `~/.districtsync` dependency), matching `loader.load_config`'s own test seam. No `flet` import — pure, display-free.
  - **DRY refactor:** `screens/setup._district_options()` currently inlines `load_config(sis_id).district_name or sis_id`. Rewrite it to call `friendly_district_name(sis_id)` so the mapping lives in exactly one place (single source of truth). Behaviour is preserved (same fallback), so `test_ui_flet_setup.py` stays green.
- **`src/ui_flet/screens/onboarding.py` (new, VIEW, coverage-omitted):** `build_onboarding(page, *, sis_type: str = "", on_start_setup: Callable[[], None]) -> ft.Control`.
  - A branded **hero card** (`components.card(gradient=components.hero_gradient(), …)`) with the product's one-line promise: *"DistrictSync keeps your MyEd BC roster flowing to SpacesEDU — automatically, every night."* When `sis_type` is set, greet by friendly name (`friendly_district_name(sis_type)`, e.g. *"SD40 - New Westminster School District"*) — never a raw id.
  - A **verdict-first status block** using `components.HealthVerdictBanner(Verdict.WARNING, headline="You're not set up yet", detail="A few quick steps and your nightly sync is running.")` — the attention (amber) tone, not alarm. This reuses the DS-1 spine (the banner's non-colour icon + tone cue is already AA-gated + tested), so onboarding inherits the verdict-never-colour-alone guarantee for free.
  - A **body card** listing the 2–3 plain first-run steps (pick your folders + district → set the nightly schedule → done) as calm copy, and the prominent CTA `components.primary_button("Start setup", lambda _e: on_start_setup(), icon=ft.Icons.ROCKET_LAUNCH_ROUNDED)` — the same rocket icon as the `setup` destination, for continuity.
  - **`on_start_setup` is a callback the shell supplies** (it calls `select_by_id("setup")`) — the view owns **no** navigation/lifecycle logic (mirrors `nav_rail`'s callback discipline). This is what makes it reusable by IA-3: IA-3 passes its own `on_start_setup`.
- **`src/ui_flet/nav_rail.py` (edit, VIEW):** add the persistent reassurance line to the rail's `trailing` column, **above** the existing Exit button. A small calm block — a `SHIELD_MOON_ROUNDED`/`VERIFIED_USER_ROUNDED` icon (muted) + wrapped caption *"Closing this window won't stop your nightly sync."* (`tokens.color_muted`, size ~11, centered, `text_align=CENTER`, width-bounded so it wraps in the ~104px rail). `build_nav` gains no new parameters — the copy is static presentation (no config, no callback). This keeps the reassurance **visible at the exact moment of the leave decision** (the admin's eye is on Exit), with zero friction.
- **`src/ui_flet/shell.py` (edit — minimal):** in `main`, after building `screens` and computing `model`, swap the `home` slot to onboarding **when unconfigured**:
  ```python
  app_cfg = AppConfig.load()            # already loaded for nav.nav_model
  unconfigured = not (app_cfg.is_complete() and app_cfg.schedule_registered)
  if unconfigured and "home" in screens:
      screens["home"] = functools.partial(
          build_onboarding, page,
          sis_type=app_cfg.sis_type,
          on_start_setup=lambda: select_by_id("setup"),
      )
  ```
  `select_by_id` is defined just below today; the `lambda` closes over it (define the swap after `select_by_id`, or bind via a tiny local — keep the ordering clean). The `setup` swap, the `DISTRICTSYNC_UI_DEMO` override, sizing, and the whole close lifecycle stay **byte-identical**. `_on_leave` stays a no-op (its docstring is updated to note the reassurance is now ambient in `nav_rail`, and IA-5 still owns the write-in-flight guard here).
- **Why swap `home`, not add a route:** the onboarding hero *is* the unconfigured Home (`0013:43` branch a). Reusing the `home` id keeps the nav model unchanged (no new destination), and IA-3 later replaces this same swap with its three-way dispatcher — onboarding becomes one branch of that dispatch. No throwaway.

### Reassurance UX — recommendation + rejected alternative
- **RECOMMENDED — persistent line near Exit (ambient).** A 2–3×/year admin opens the cockpit to *check* the sync, then closes it. A **confirm-on-exit dialog fires on every close** — after the third time it is pure friction, and worse, it subtly implies closing *is* risky (the opposite of the reassurance's intent). A calm, always-visible line above Exit delivers the same message **without interrupting**, is read precisely when the leave decision is made, costs no extra click, and cannot become an annoyance. It also needs no lifecycle wiring — it's static copy in the rail the shell already builds.
- **ALTERNATIVE (noted, rejected) — confirm-on-exit `ft.AlertDialog`.** Wire `do_exit` to `page.show_dialog(...)` with a "Close DistrictSync? Your nightly sync keeps running." dialog + Cancel/Close. *Pro:* impossible to miss; guarantees the message is seen once. *Con:* fires every close (annoyance for a repeat user), implies risk where there is none, and adds a real close-path branch (more surface to keep the zero-orphan close correct through). Deferred behind a field signal — adopt only if partners report genuine "did closing break it?" confusion (the same YAGNI bar the single-instance guard sits behind). If ever needed, `_on_leave`/`do_exit` is the seam.

## Architecture & holistic fit
- **Codebase fit** — realizes IA model branch (a) as a reusable factory (`build_onboarding`), reuses the DS-1 verdict spine (`HealthVerdictBanner`) + component primitives verbatim, and folds DS-2 as a genuinely-pure helper with a real first consumer + a DRY cleanup (setup's inline lookup collapses into it). Tiers cleanly: pure `humanize` (COUNTED) → `onboarding`/`nav_rail`/`shell` views (omitted). Zero core touch → architect gate trivially met.
- **Quality dimensions:** `product-ux` (verdict-first calm onboarding; plain "not set up yet"; humanized district name — never a raw id; reassurance at the leave point without friction) · `maintainability-structure` (onboarding as a callback-driven factory IA-3 reuses; `district_name` single-sourced in `humanize`; view/pure tiering held) · `reliability-resilience` (`friendly_district_name` is total — a bad/unknown config falls back to the raw id, never crashes the hero; the shell still never crashes) · `testing` (the pure helper COUNTED + fixture-tested; views manually verified) · **`privacy` (LIVE/top):** onboarding + reassurance copy is generic product voice — **no student PII, no real paths, no secrets**; `district_name` is public config metadata, not PII.
- **Future-proofing** — IA-3 imports `build_onboarding` for branch (a) and passes its own `on_start_setup`; IA-5/6/9 import `friendly_district_name` (2nd+ consumers — promotes the DS-2 helper's value). The reassurance line is a stable rail fixture every surface inherits.

## Affected files
- `src/ui_flet/humanize.py` — **new (PURE, COUNTED)**: `friendly_district_name(sis_type, *, config_dir=None)` — DS-2's helper; total, fallback-to-raw-id, `config_dir` test seam; no flet import.
- `src/ui_flet/screens/onboarding.py` — **new (view, coverage-omitted)**: `build_onboarding(page, *, sis_type="", on_start_setup)` — the reusable first-run hero (branded hero card + `HealthVerdictBanner(WARNING)` status + steps + "Start setup" CTA); owns no nav/lifecycle (callback-driven). IA-3 branch (a) reuses it.
- `src/ui_flet/nav_rail.py` — **edit (view)**: add the persistent decouple-the-sync reassurance line to the rail `trailing`, above Exit (static copy; no new `build_nav` param).
- `src/ui_flet/shell.py` — **edit (minimal)**: swap the `home` slot to `build_onboarding` when unconfigured (same `functools.partial` pattern as `setup`); update `_on_leave`'s docstring (reassurance now ambient in `nav_rail`; write-guard still IA-5). Close lifecycle + `setup` swap + demo override byte-identical.
- `src/ui_flet/screens/setup.py` — **edit (DRY)**: `_district_options()` calls `humanize.friendly_district_name(sis_id)` instead of the inline `load_config(...).district_name or sis_id`. Behaviour preserved.
- `pyproject.toml` — **edit**: add `src/ui_flet/screens/onboarding.py` to `[tool.coverage.run] omit` (view). `humanize.py` stays COUNTED (NOT omitted).
- `tests/test_ui_flet_humanize.py` — **new (COUNTED)**: unit tests for `friendly_district_name` (see *Test strategy*).
- `docs/claugentic-ARCHITECTURE_TREE.md` — **add** `humanize.py` + `screens/onboarding.py`; update `nav_rail.py` (reassurance line) + `shell.py` (unconfigured `home` swap) + `screens/setup.py` (reuses `humanize`) descriptions.
- `docs/claugentic-DECISIONS.md` — **edit (at Land, by the orchestrator)**: IA-2 one-liner (onboarding factory reused by IA-3; reassurance = ambient line near Exit + why not a dialog; DS-2 folded into `humanize`).

## Risks & mitigations
- **Onboarding shape pre-empts IA-3's Home design** → build it strictly as a **callback-driven factory** (`on_start_setup` injected, no config read beyond the passed `sis_type`, no verdict *derivation*), so IA-3 wires it as branch (a) with no rewrite. IA-2 owns only branch (a)'s content; the three-way dispatch + status module are explicitly IA-3 (Non-goal).
- **`friendly_district_name` crashing the hero on a bad config** → the helper is **total**: catches every load failure and falls back to the raw `sis_type`; empty input → `""`. Covered by the unknown-id + broken-config unit cases. The hero renders regardless.
- **DRY refactor of `setup._district_options` regresses the dropdown** → the *logic* (the `district_name`-or-id fallback + warning log) moves into `humanize.friendly_district_name`, which is **COUNTED + unit-tested**, so the behaviour is covered at its new home. Note honestly: `tests/test_ui_flet_setup.py` does **NOT** cover `_district_options` (it tests only the pure `filepicker.setup_state` gate), and `_district_options` lives in the coverage-**omitted** `screens/setup.py` — so the residual risk is only that the call-site is wired correctly, caught by the manual smoke (not by `test_ui_flet_setup.py`). Pure substitution, same fallback.
- **Reassurance-vs-annoyance** → resolved in *Approach*: the persistent-line choice cannot become an annoyance (no per-close interruption); the dialog alternative is deferred behind a field signal (YAGNI), with `_on_leave`/`do_exit` as the ready seam if it's ever needed.
- **`home` swap breaks the configured path** → the swap is gated on `unconfigured` (the exact `is_complete() and schedule_registered` predicate `nav._prominent_group` uses); configured installs keep the `home` placeholder untouched until IA-3. A single boolean read — no new state.
- **Coverage** → `humanize.py` COUNTED (carries the 80% gate); only `screens/onboarding.py` added to the omit list (matches the established `shell/nav_rail/components/picker_field/setup` view-omit pattern).
- **Zero core touch** → no `src/etl|config|sftp|scheduler|main.py` diff; `humanize` only *reads* `config/loader` (adds nothing to it) — architect core-untouched gate holds.

## Test strategy
- **Unit (COUNTED) — `tests/test_ui_flet_humanize.py`** (`friendly_district_name`, using the `config_dir` fixture seam over the real `config/mappings/`):
  - **known district → friendly name:** a real SIS with a `district_name` returns the human name, not the id — assert against the **actual YAML value** (all 9 bundled configs carry one; e.g. `sd40myedbc` → `"SD40 - New Westminster School District"`). Prefer asserting `result != sis_type and result` over hardcoding the string, to stay robust to config-copy edits.
  - **config without a `district_name` → falls back to the raw id** — use a **fixture `config_dir`** with a mapping whose `district_name` is `""` (no bundled config has an empty one today, so this MUST be a fixture, not a real config).
  - **unknown id → raw id** (a `sis_type` with no mapping file → returns the input unchanged, no raise).
  - **empty / whitespace → `""`** (no district chosen → generic hero copy).
  - **broken/unloadable config → raw id** (point `config_dir` at a fixture with a malformed YAML for the id → caught, falls back, no raise).
  - **never surfaces a raw id when a friendly name exists** (the product invariant: known-district path never returns the id).
- **DRY regression:** `pytest tests/test_ui_flet_setup.py` stays green after the rewire, but note it does **not** cover `_district_options` (it tests only `setup_state`); the rewired behaviour is covered by the `friendly_district_name` unit cases (its new home) + the manual dropdown smoke.
- **Manual (`DISTRICTSYNC_UI=flet`, `.claude/settings.json` dev default):**
  1. **Unconfigured launch** (fresh / no `~/.districtsync/config.json`): rail leads with Setup (IA-1); click **Home** → the **onboarding hero** renders — one-line promise, amber "You're not set up yet" verdict banner, "Start setup" CTA. Click **Start setup** → navigates to the Setup surface (native rail highlight follows).
  2. **District greeted by name:** with a `sis_type` saved but not yet scheduled, the hero greets the friendly district name (never `sd48myedbc`).
  3. **Reassurance line:** the "Closing this window won't stop your nightly sync." line is visible in the rail directly above **Exit**, in every state, and wraps cleanly at the rail width.
  4. **Configured + scheduled** (fake `schedule_registered=True` in config): rail leads with Home; `home` shows the **placeholder** (not onboarding) — confirming the swap is unconfigured-only and IA-3 owns the real dashboard.
  5. **Exit + window-close** still tear down clean (zero-orphan) — unchanged.
- **Regression:** full `pytest` (80% gate) + SD74 snapshot + `ruff check`/`ruff format --check` + `mypy src/ --exclude 'src/ui'` (no NEW errors; the pre-existing `classes.py:130` pandas-stubs error is not ours) + `bandit -r src/ -q` + `make validate-config` green; architect core-untouched (zero `src/etl|config|sftp|scheduler|main.py` diff); tree-check `python scripts/claugentic-check_architecture_tree.py --staged` passes with the two new files described. The PLAT-3 windowed-exe smoke still opens + zero-orphan closes.

## Decomposition (slices)
**ONE slice** — the onboarding hero (reusable factory) + the reassurance line + the DS-2 `humanize` helper (with the setup DRY cleanup) land together, complete + tested, no half-state: every surface stays reachable, the nav model/rule is unchanged, and onboarding is wired into the `home` slot for the unconfigured admin. The three sub-parts are one coherent user story (a first-run admin's *complete* first experience — welcomed, told the sync is safe, and shown their district by name) and share the humanize helper, so splitting would leave a half-built onboarding.
- [ ] **IA-2 — first-run onboarding hero + decouple-the-sync reassurance (folds DS-2)** · `humanize.friendly_district_name` (pure, COUNTED) + setup DRY reuse · `screens/onboarding.py` (reusable hero from DS-1 components + `Verdict.WARNING` banner + Start-setup CTA callback) · `nav_rail.py` reassurance line above Exit · `shell.py` unconfigured `home` swap + `_on_leave` docstring · tests · tree/decisions. **Lands complete:** a first-run admin opening DistrictSync sees a calm, branded, verdict-first welcome that names their district and points them to Setup, is reassured the nightly sync is independent of the window, and the pieces are shaped so **IA-3 reuses onboarding as Home branch (a)** and **IA-5/6/9 reuse `friendly_district_name`** — **no debt** (the three-way Home dashboard + status derivation are IA-3, explicitly out of scope).

---
## Review

`RUNNING AS: Opus 4.x` — *a separate clean-context plan-gate pass on the most capable model; a reduction of rubber-stamping risk, not an independent-model guarantee. This same-model run does not de-correlate blind spots.*

**Verdict: CHANGES REQUIRED** (M/low — proportionate, not gold-plated). The spine is sound and I'm not sending it back to the drawing board: the pure/view tiering is right, the DS-2 fold is faithful to `0013:54` (pure tested helper, first consumer + a real DRY cleanup — not a silent scope change), the reassurance UX call (persistent ambient line over confirm-dialog) is well-reasoned and correctly rejects the annoying alternative, the onboarding factory's callback seam is genuinely IA-3-reusable, and the `home`-slot swap (vs a new nav destination) is the correct minimal wiring. YAGNI is respected — this is `friendly_district_name`, not a humanization framework. The changes below are **accuracy/completeness tightenings and two decisions to force into the Spec**, not a design rejection.

### Required changes (numbered, actionable)

1. **Correct the DRY-refactor regression claim — `test_ui_flet_setup.py` does NOT cover `_district_options`. [testing — accuracy, must fix in the plan text]**
   Risks (line 73) and Test strategy (line 87) both assert that re-running `tests/test_ui_flet_setup.py` "confirms" the `_district_options` → `humanize` rewire. It does not: that file tests only the pure `filepicker.setup_state` gate (verified — `tests/test_ui_flet_setup.py:1-87` never imports or calls `_district_options`), and `_district_options` lives in the coverage-**omitted** `screens/setup.py`, so the substitution has **zero automated regression coverage**. The mitigating truth is real and should be stated instead: the *logic* moves into `humanize.friendly_district_name`, which is COUNTED + unit-tested, so the behaviour is covered at its new home; the residual risk is only that the call-site is wired correctly, caught by the manual smoke (Test strategy #1/#2) — not by `test_ui_flet_setup.py`. Fix the two lines to say that. (No new test demanded — the helper's own cases cover the behaviour; don't gold-plate a view-glue substitution.)

2. **Force the "does the ambient line cover the OS-window-close leave path?" decision in the Spec. [product-ux — the one genuine gap in the reassurance placement]**
   `0013:43` mandates the reassurance "appears at **every** leave point." The recommended persistent line sits in the rail `trailing`, directly above the in-app **Exit** button — but the admin's other leave path is the **OS title-bar ✕** (top-right, outside the rail). The plan's own logic ("read precisely when the leave decision is made", line 51) is strongest for the Exit button and weakest for the ✕. Two honest resolutions, either acceptable — the Spec must pick one and say why: **(a)** argue the line is *always on-screen* whenever the window is open, so it is present regardless of which leave path is taken (a defensible reading of "every leave point" for an ambient cue — but then soften line 51's "eye is on Exit" framing, which under-claims coverage of the ✕); or **(b)** if "every leave point" is read strictly, note that the ✕ path is covered ambiently-but-not-adjacently and that `_on_leave`/`do_exit` remain the seam if a per-close cue is ever field-justified (the same YAGNI bar the rejected dialog sits behind). Do **not** leave this implicit — it's the load-bearing product claim of the reassurance half of the slice.

3. **Pin the empty-`district_name` unit case as a REQUIRED fixture, not an optional one. [testing — the plan already flags it; make it non-negotiable in the Spec]**
   Verified: all **9** bundled configs carry a non-empty `district_name` (`config/mappings/*_mapping.yaml` — none empty), so the "config with `district_name: ''` → falls back to raw id" branch (Approach line 27, the `iff non-empty` clause) is unreachable by any real config and would be **uncovered without a synthetic fixture**. The plan correctly notes this (Test strategy line 82) — the Spec must make it a hard requirement (a `config_dir` fixture with a `district_name: ""` mapping), because it exercises a distinct branch from the unknown-id and broken-config cases. Likewise pin the **broken-YAML** fixture case (line 85) so the `except → raw id` path is genuinely hit, not just asserted.

4. **State the shell-edit ordering resolution concretely (don't leave it as "or"). [maintainability-structure — remove the deferred decision from a "lands complete" slice]**
   The swap snippet (lines 37-47) reads `app_cfg.is_complete()` but today `shell.py:150` calls `AppConfig.load()` inline without holding a reference — the Spec must hoist it to a named `app_cfg`. And the `on_start_setup=lambda: select_by_id("setup")` closure references `select_by_id`, which is defined *below* the `screens` block (`shell.py:173`). Python resolves the free name at call-time (button click), so defining the swap in the `screens` block is safe — but the plan offers "define after `select_by_id`, or bind a local" as an unresolved either/or (line 47). Pick one in the Spec (recommend: perform the `home` swap in the same `screens[...] = functools.partial(...)` block right after the `setup` swap — the lambda's late binding is correct and keeps all screen-map mutation co-located). A "lands complete" slice shouldn't carry a coin-flip into build.

5. **Onboarding empty/error-state honesty — one line in the Spec. [product-ux — you asked; answer it]**
   The onboarding hero has no live-data dependency (its verdict is a fixed `WARNING`, its only input is the already-loaded `sis_type`), so it has **no empty/loading/error state of its own** — the classic "status unavailable" degradation is IA-3's (correctly a Non-goal). The one place it *can* degrade is `friendly_district_name` on a bad saved `sis_type`, and the plan makes that total (falls back to the raw id, never blank/crash). State this explicitly in the Spec so the reviewer/architect doesn't flag a "missing empty/loading state" — the honest answer is "N/A for a static hero; the only failure axis is the district-name lookup, which is total." (This is the *right* amount of state handling for this surface — resist adding speculative loading skeletons.)

### Sizing / completeness check
- **IA-2 (single slice) — OK. Correctly one session, lands complete, zero debt.** The three sub-parts (humanize + onboarding + reassurance) are one coherent first-run user story sharing the `humanize` helper; splitting would leave a half-built onboarding (a hero that can't name its district, or a district helper with no consumer). No half-state: every surface stays reachable, the nav model/rule is untouched, and the swap is a single gated boolean read. The pure/view tiering matches the established pattern exactly — `humanize.py` COUNTED (carries the gate), `screens/onboarding.py` added to the `pyproject.toml` omit list alongside the existing `shell/nav_rail/components/picker_field/setup` view-omits (verified that list at `pyproject.toml:80-89`). The `unconfigured` predicate `not (is_complete() and schedule_registered)` is De-Morgan-identical to `nav._prominent_group`'s `not is_complete() or not schedule_registered` (verified `src/ui_flet/nav.py:73`) — genuinely single-sourced intent, no drift. Boundary with IA-3 is crisp: IA-2 builds branch (a)'s *content* only, reads no run-history/staleness/task-state, derives no verdict. **No sizing change needed.**

### Harness impact
- **No new STANDARD or agent.** Touches `docs/claugentic-ARCHITECTURE_TREE.md` (add `humanize.py` + `screens/onboarding.py`; update `nav_rail.py`/`shell.py`/`screens/setup.py` descriptions — already listed, and required in-commit by the tree-check gate) and `docs/claugentic-DECISIONS.md` at Land (already listed).
- **One DECISIONS content requirement:** the reassurance-placement ruling from change #2 and the DS-2-folded-into-`humanize` decision must land as the dated one-liner (already scoped at Affected-files line 68) — record *why* ambient-line-over-dialog and *how* the OS-✕ leave path is covered, so IA-5 (which attaches the write-guard to the same `_on_leave` seam) and IA-9 (the broader humanization sweep) inherit the reasoning rather than re-litigate it.

### KEPT spine (sound — do not churn)
- **`humanize.py` as the home for `friendly_district_name`** (not extending `nav`/`tokens`) — correct: it's config-domain presentation logic with `config/loader` as its source and multiple future consumers (IA-3/5/6/9); a flet-free pure module with a `config_dir` test seam mirroring `loader.load_config`. Keep.
- **Reusing the DS-1 verdict spine** (`HealthVerdictBanner(Verdict.WARNING, headline=…)`) — sound; the headline is overridable (`components.py:207`), so "You're not set up yet" cleanly replaces the default "Needs your attention" while inheriting the AA-gated never-colour-alone guarantee for free. Keep.
- **`home`-slot swap via `functools.partial` (not a new nav destination)** — correct; reuses the exact `setup`-swap pattern (`shell.py:156`), keeps the nav model unchanged, and IA-3 replaces this same swap with its three-way dispatcher (no throwaway). Keep.
- **`friendly_district_name` totality** (empty → `""`; unknown/broken → raw id; catches every load failure, warns like `setup._district_options`) — the right resilience contract for a trust surface; the hero renders regardless. Keep.
- **Persistent-line-over-confirm-dialog** (subject only to change #2's ✕-coverage note) — the reasoning (a 2–3×/yr admin; per-close friction; a dialog implies risk where there is none) is correct and the dialog is deferred behind a field signal at the ready `_on_leave`/`do_exit` seam. Keep.
- **DS-2 fold faithfulness** — verified against `0013:54`: pure + tested helper, kept minimal (only `district_name`), IA-9 owns the broader sweep. Not a scope expansion. Keep.

## Spec

> Folds the plan-gate ruling (see `## Review`). All 5 required changes resolved below; the spine (pure `humanize` + reusable onboarding factory + ambient reassurance line + `home`-slot swap) is unchanged.

### 1. `src/ui_flet/humanize.py` — new PURE module (COUNTED, no flet import)

```python
"""Humanization helpers for the Flet UI — turn machine ids into the words an admin speaks.

PURE + COUNTED (no flet import): trust-surface copy must never show a raw config id
(``sd48myedbc``) to a non-technical admin. IA-2 needs only ``friendly_district_name``;
IA-9 grows the broader sweep (timestamps, no raw paths/filenames/stack traces). Kept
minimal by design (YAGNI) — this is one total function, not a framework.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def friendly_district_name(sis_type: str, *, config_dir: Path | None = None) -> str:
    """Map a SIS id to its human ``district_name``; TOTAL — never raises, never crashes a view.

    - empty/whitespace ``sis_type`` → ``""`` (no district chosen → generic hero copy).
    - else load the config and return its ``district_name`` stripped IFF non-empty,
      otherwise fall back to the raw ``sis_type``.
    - any load failure (FileNotFoundError / ValueError / unexpected) → fall back to the
      raw ``sis_type`` (an admin sees ``sd48myedbc`` at worst, never a blank or a crash),
      logged at ``warning`` (mirrors ``screens/setup._district_options``).

    ``config_dir`` is a test seam (passthrough to ``loader.load_config``) so this is
    unit-testable against a fixture mappings dir without a ``~/.districtsync`` dependency.
    """
    sis = sis_type.strip()
    if not sis:
        return ""
    try:
        from src.config.loader import load_config
        cfg = load_config(sis, config_dir) if config_dir is not None else load_config(sis)
        name = (cfg.district_name or "").strip()
        return name if name else sis
    except Exception as exc:  # total: any load failure falls back to the raw id
        logger.warning("friendly_district_name(%r) fell back to the raw id: %s", sis, exc)
        return sis
```

**Verify `load_config`'s real signature** before building (the implementer reads `src/config/loader.py`): the `config_dir` passthrough must match how `screens/setup._district_options` / `available_configs` already call it. If `load_config` does not take a dir arg, use the same seam `loader` exposes for tests (match the existing call form exactly — do not invent one). The behaviour contract above is fixed regardless of the exact seam.

**DRY:** rewrite `screens/setup._district_options()` to call `friendly_district_name(sis_id)` in place of the inline `load_config(sis_id).district_name or sis_id`, so the mapping lives in exactly one place. Behaviour preserved (same fallback + warning).

### 2. `src/ui_flet/screens/onboarding.py` — new VIEW (coverage-omitted)

`build_onboarding(page, *, sis_type: str = "", on_start_setup: Callable[[], None]) -> ft.Control`. Assemble ENTIRELY from `components.py` + `verdict`/`tokens` (never hand-rolled — `FilledButton(text=)` trap):
- **Hero card** — `components.card(gradient=components.hero_gradient(), …)`; one-line promise *"DistrictSync keeps your MyEd BC roster flowing to SpacesEDU — automatically, every night."* When `sis_type` is non-empty, greet by `humanize.friendly_district_name(sis_type)` (never a raw id); when empty, generic copy (no name).
- **Verdict-first status** — `components.HealthVerdictBanner(Verdict.WARNING, headline="You're not set up yet", detail="A few quick steps and your nightly sync is running.")` (amber attention tone; inherits the DS-1 never-colour-alone icon+tone cue).
- **Body card** — 2–3 plain first-run steps (pick folders + district → set the nightly schedule → done) + the CTA `components.primary_button("Start setup", lambda _e: on_start_setup(), icon=ft.Icons.ROCKET_LAUNCH_ROUNDED)`.
- **`on_start_setup` is an injected callback** — the view owns NO navigation/lifecycle (mirrors `nav_rail`). This is what makes IA-3 reuse it verbatim.
- **State honesty (gate #5):** the hero is static — it has **NO empty/loading/error state of its own**. Its only failure axis is `friendly_district_name`, which is **total** (falls back to the raw id, never blank/crash). "status unavailable" graceful-degradation is **IA-3's** (Non-goal here). Do **NOT** add a speculative loading skeleton — that would be over-building a display-free static surface.

### 3. `src/ui_flet/nav_rail.py` — edit (VIEW): the reassurance line

Add a persistent reassurance block to the rail's `trailing` column, **above** the existing Exit button: a muted icon (`ft.Icons.VERIFIED_USER_ROUNDED` or `SHIELD_MOON_ROUNDED`) + wrapped caption *"Closing this window won't stop your nightly sync."* (`tokens.color_muted`, size ~11, `text_align=ft.TextAlign.CENTER`, width-bounded so it wraps in the ~104px rail). Static presentation — `build_nav` gains **no** new parameter.

**OS-window-close ✕ coverage (gate #2) — resolution (a), adopted:** the reassurance line is **always on-screen whenever the window is open**, so it is present regardless of which leave path the admin takes — the in-app **Exit** *or* the OS title-bar **✕**. That satisfies `0013:43`'s "every leave point" for an *ambient* cue: the message is continuously visible, not attached to one control. (Soften any "the admin's eye is on Exit" framing accordingly — the claim is *always-visible*, not *Exit-adjacent-only*.) A per-close cue tied to the ✕ specifically stays deferred behind a field signal at the `_on_leave`/`do_exit` seam (the same YAGNI bar as the rejected dialog). This ruling goes into the DECISIONS one-liner.

### 4. `src/ui_flet/shell.py` — edit (minimal): hoist `app_cfg`, swap `home` in the screens block (gate #4)

Concrete ordering (no either/or):
- **Hoist** the config load: replace the inline `nav.nav_model(AppConfig.load())` with a named `app_cfg = AppConfig.load()` then `model = nav.nav_model(app_cfg)`.
- Do the `home` swap **in the same `screens[...] = functools.partial(...)` block, right after the `setup` swap** (co-locating all screen-map mutation). The `on_start_setup=lambda: select_by_id("setup")` closure references `select_by_id`, defined below — Python resolves the free name at **call-time** (button click), so this late binding is correct:
  ```python
  screens["setup"] = functools.partial(build_setup, page)   # existing
  if not (app_cfg.is_complete() and app_cfg.schedule_registered) and "home" in screens:
      screens["home"] = functools.partial(
          build_onboarding, page,
          sis_type=app_cfg.sis_type,
          on_start_setup=lambda: select_by_id("setup"),
      )
  ```
- The `DISTRICTSYNC_UI_DEMO` override, window sizing, and the entire close lifecycle stay **byte-identical**. Update `_on_leave`'s docstring to note the reassurance is now ambient in `nav_rail` (IA-5 still owns the write-in-flight guard at this seam) — the function stays a no-op.
- Import `build_onboarding` from `src.ui_flet.screens.onboarding`.

### 5. `pyproject.toml`
Add `"src/ui_flet/screens/onboarding.py",` to `[tool.coverage.run] omit` (view). **`humanize.py` stays COUNTED** (NOT omitted).

### 6. `tests/test_ui_flet_humanize.py` — new (COUNTED)
Cover `friendly_district_name` via the `config_dir` seam over the real `config/mappings/` **plus required synthetic fixtures (gate #3)**:
- **known district → friendly name:** a real bundled SIS with a `district_name` returns the human name, not the id — assert `result != sis_type and result` (robust to config-copy edits; optionally also assert the actual value, e.g. `sd40myedbc` → the real YAML string).
- **empty `district_name` → raw id [REQUIRED FIXTURE]:** a `config_dir` fixture with a mapping whose `district_name: ""` — no bundled config has an empty one, so this branch (the `iff non-empty` clause) is **unreachable without a fixture**. Must be a synthetic fixture.
- **broken/unloadable config → raw id [REQUIRED FIXTURE]:** a `config_dir` fixture with malformed YAML for the id → the `except` path is genuinely hit (falls back, no raise).
- **unknown id → raw id** (no mapping file → input returned unchanged, no raise).
- **empty / whitespace → `""`**.
- **product invariant:** the known-district path never returns the raw id when a friendly name exists.

### 7. `docs/claugentic-ARCHITECTURE_TREE.md`
Add `src/ui_flet/humanize.py` + `src/ui_flet/screens/onboarding.py`; update `nav_rail.py` (reassurance line), `shell.py` (unconfigured `home` swap), `screens/setup.py` (reuses `humanize`) descriptions. (Tree-check gate aborts the commit otherwise — the implementer authors the descriptions.)

### 8. `docs/claugentic-DECISIONS.md` (at Land, by the orchestrator)
IA-2 one-liner: onboarding factory (reused by IA-3 branch a); reassurance = **ambient always-on-screen line above Exit** and **why** (2–3×/yr admin; per-close dialog = friction + implies risk) + **how the OS-✕ path is covered** (always-visible ambient cue — resolution (a)); DS-2 folded into pure `humanize.friendly_district_name` (first consumer onboarding + setup DRY reuse; IA-9 owns the broader sweep); zero core.

### Build discipline (implementer)
- Branch: create `feat/0019-flet-ia2-onboarding` off `feat/flet-ui-rebuild` (HEAD `aa9150b`). **Commit early** once files compile + gates pass.
- Stage ONLY these by explicit path — **never** `git add -A`:
  `.claude/plans/0019-flet-ia2-onboarding.md  src/ui_flet/humanize.py  src/ui_flet/screens/onboarding.py  src/ui_flet/nav_rail.py  src/ui_flet/shell.py  src/ui_flet/screens/setup.py  pyproject.toml  tests/test_ui_flet_humanize.py  docs/claugentic-ARCHITECTURE_TREE.md`
  (DECISIONS at Land by the orchestrator.) **Never** stage the pre-existing noise (`.claude/settings.json`, `CLAUDE.md`, `docs/claugentic-ENGINEERING_STANDARDS.md`, `docs/claugentic-WORKFLOW.md`, `docs/claugentic-standards/README.md`, `scripts/claugentic-check_architecture_tree.py`, `.githooks/`, `docs/claugentic-PLAN_TEMPLATE.md`). After `git add`, run `git status --porcelain` and confirm those stay unstaged.
- **Zero** change to `src/etl|config|sftp|scheduler|main.py` (`humanize` only *reads* `config/loader`).
- All buttons/cards via `components.py`.
- Gates before commit: `pytest` full (80% gate) · `ruff check src/ tests/` + `ruff format --check src/ tests/` · `mypy src/ --exclude 'src/ui'` (no NEW errors; `classes.py:130` pandas-stubs is pre-existing, not yours) · `bandit -r src/ -q` · `make validate-config` · tree-check `python scripts/claugentic-check_architecture_tree.py --staged`.
