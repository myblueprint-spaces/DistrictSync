<!-- claugentic-dev-harness@0.3.0 -->
# 0018 — IA-1: shell assembly + state-aware grouped navigation (live)

- **Status:** DRAFT → adversarial gate → Spec → build
- **Parent program:** [`0013-flet-production-redesign.md`](0013-flet-production-redesign.md) (IA-1 row) · follows DS-1 (`128fa13`), PLAT-2 (`70587ea`), PLAT-3 (`546a200`)
- **References:** `src/ui_flet/nav.py` (the pure model — `NavGroup`/`Destination`/`DESTINATIONS`/`nav_model` → `groups` + `prominent_group`, built+tested in PLAT-1 *so IA-1 is a render change*) · `src/ui_flet/shell.py` (the flat `NavigationRail` + `build_screens`/`render`/`select` to refactor) · `src/ui_flet/components.py` (DS-1 primitives) · `src/config/app_config.py` (`is_complete()`/`schedule_registered` drive prominence)

## Problem
The shell renders a **flat** `NavigationRail` (all six destinations in `DESTINATIONS` order, no grouping, no state awareness) — explicitly deferred in PLAT-1 ("the rail renders flat this slice; prominence wiring is IA-1"). The pure nav model already computes the **groups** (Get started / Everyday / Advanced) and the **prominent group** (Get-started until `is_complete()` + `schedule_registered`, then Everyday), but nothing renders them — so an unconfigured admin isn't led to Setup, and a configured one isn't led to their Everyday cockpit. Flet's `ft.NavigationRail` is a flat destinations list with **no section headers**. Per the plan-gate ruling (see `## Review` #1), the state-aware design is realized by **reordering that single flat rail** so the prominent group's destinations lead — **not** a purpose-built nav (custom-nav keyboard-focus/a11y is unverified on 0.85.3; the program mandates the *control* + "grouped/prominent," never header text). This is also the documented point to **split `shell.py`** before the IA surfaces grow it further (the F6 boundary note from PLAT-1/DS-1: window + rail + host + lifecycle co-live there today).

## Goals / Non-goals
> _Revised post-gate (2026-07-03) to fold the `## Review` ruling — option (a) reorder-flat, no headers, built-in a11y._
- **Goal — state-aware ordering:** reorder the flat `ft.NavigationRail` so the **prominent group's destinations lead** and the **initial selected destination** is the prominent group's first — unconfigured → **Setup** leads + selected; configured+scheduled → **Home** leads + selected. Verdict-first, calm, branded. **No section headers** (gate #1).
- **Goal — render of the pure model:** add PURE, tested helpers to `nav.py` — `ordered_destinations(model)` (flat destination list, prominent group first, empty groups dropped) + `prominent_initial_id(model)` (the initial selection, total). The view consumes them; no new "configured" logic (reuse the model + rule PLAT-1 fixed + tested).
- **Goal — F6 split (right-sized):** extract the ~60-line rail VIEW (brand mark + rail + destinations + Exit trailing) into `src/ui_flet/nav_rail.py`, slimming `shell.py` to assembly + lifecycle. **Reconfirmed warranted (gate #5):** a genuine view extraction, not a module manufactured for a 10-line reorder — the reorder *logic* is the pure `nav.py` helper; `nav_rail.py` is the rail *view* that leaves shell.
- **Goal — a11y baseline (0013):** met by **retaining `ft.NavigationRail`** — keyboard traversal, selection semantics, and a focus indicator are built-in on 0.85.3; **no custom focus code** (which would be unverified). Selection is never colour-alone (icon + label + indicator). Full screen-reader certification stays deferred (localhost, Flutter canvas).
- **Non-goal:** new surface *content* — Home three-way dashboard (IA-3), onboarding (IA-2), the other ports (IA-4..8) — the placeholders/landed Setup stay; IA-1 changes only how they're *navigated to*.
- **Non-goal:** section headers / stacked-rails / custom nav — deferred behind a future time-boxed a11y spike **only if** headers ever become required (gate #1). Changing the nav model's group membership or the prominence *rule* (PLAT-1 fixed + tested those) — IA-1 renders them.
- **Non-goal:** touching the ETL/CLI core. Zero `src/etl|config|sftp|scheduler|main.py` change.

## Approach
> _Revised post-gate (2026-07-03) — option (a) reorder-flat rail; `ordered_groups` → flat `ordered_destinations`; native highlight._
- **`nav.py` (edit, COUNTED):** add two pure helpers over the existing model (no flet import, ordering frozen dataclasses):
  - `ordered_destinations(model) -> tuple[Destination, ...]` — the flat destination list with `model.prominent_group` **moved to the front**, remaining groups in canonical `NavGroup` display order, **empty groups dropped**, within-group order preserved. Total (an empty prominent group contributes nothing).
  - `prominent_initial_id(model) -> str` — the id of the prominent group's **first** destination; **total** — falls back to the first destination of `ordered_destinations(model)` when the prominent group is empty, and to `""` only if there are no destinations at all.
  Keep `DESTINATIONS`/`NavGroup`/`nav_model` as-is.
- **`src/ui_flet/nav_rail.py` (new, view — coverage-omitted):** `build_nav(*, ordered, selected_id, on_select, on_exit) -> ft.Control` builds the themed flat `ft.NavigationRail` from `ordered` (brand mark as `leading`, one `ft.NavigationRailDestination` per entry, **Exit** in `trailing` via `on_exit`), computes `selected_index` = position of `selected_id` in `ordered` (fallback 0), and wires `on_change` → `on_select(ordered[e.control.selected_index].id)`. Returns the bordered rail wrapper. **Owns NO lifecycle** (gate #3) — `on_select`/`on_exit` are callbacks; the brand mark is pure presentation. Selection is by **`dest.id`**, decoupling render order from the screen map.
- **`shell.py` (edit — slimmed):** drop the inline `ft.NavigationRail` (+ its `leading` brand block + `exit_btn`/`trailing`), now in `nav_rail`, and the index-based `render`/`select`/`on_nav_change`. Keep window paint + sizing + `content_host` + close lifecycle (`do_exit`/`_on_leave`/`on_window_event`/`on_disconnect`) + the `functools.partial(build_setup, page)` swap + `DISTRICTSYNC_UI_DEMO` override — **byte-identical**. `render_by_id`/`select_by_id` re-key on **`dest.id`** (the `screens` dict is already id-keyed); build `ordered = nav.ordered_destinations(model)` + `initial = nav.prominent_initial_id(model)`; `nav_rail.build_nav(ordered=…, selected_id=initial, on_select=select_by_id, on_exit=do_exit)` replaces the rail; initial `render_by_id(initial)`.
- **Re-render mechanism (pinned, gate #4):** the highlight is **native** — `ft.NavigationRail` updates its own `selected_index` on click; the shell sets only the **initial** index (via `selected_id`) and renders the selected screen on `on_change`. No manual per-button style toggle, no nav rebuild.
- **Prominence semantics:** "led" = the prominent group's destinations render **first** (top of the rail); the **initial selected screen** is the prominent group's first destination (unconfigured → Setup; configured → Home) instead of always index 0.

## Architecture & holistic fit
- **Codebase fit** — realizes the PLAT-1 model exactly as intended (render change, not logic); tiers cleanly (pure `nav` ordering → `nav_rail` view → `shell` assembly); resolves the F6 `shell.py` boundary split. Zero core touch → architect gate trivially met.
- **Quality dimensions:** `product-ux` (state-aware leading; grouped legibility; verdict-first calm; selection never colour-alone) · `maintainability-structure` (nav extracted; shell slimmed; render-ordering single-sourced + tested in `nav.py`) · `testing` (pure `ordered_groups` + the prominent-initial-selection logic counted; view omitted) · `reliability-resilience` (the shell still never crashes; nav degrades to the full list if a group is empty).
- **Future-proofing** — IA-2 (onboarding) + IA-3 (Home health) drop into the same `content_host`; the grouped nav + `dest.id` selection is the stable frame they navigate within; adding a destination = a `DESTINATIONS` entry (model) the nav renders automatically.

## Affected files
- `src/ui_flet/nav.py` — **edit (COUNTED)**: add pure `ordered_destinations(model)` + `prominent_initial_id(model)`; keep the existing model.
- `src/ui_flet/nav_rail.py` — **new (view, coverage-omitted)**: `build_nav(*, ordered, selected_id, on_select, on_exit)` — the flat state-aware rail from DS-1 components + the brand mark + Exit; no lifecycle.
- `src/ui_flet/shell.py` — **edit**: drop the inline rail/brand/exit + the index-based render/select; consume `nav_rail`; re-key render/select on `dest.id`; set initial selection from `prominent_initial_id`; slim to assembly + lifecycle.
- `pyproject.toml` — add `src/ui_flet/nav_rail.py` to `[tool.coverage.run] omit` (view).
- `tests/test_ui_flet_nav.py` — **edit**: add `ordered_destinations` cases (unconfigured→Setup/Get-started first; configured+scheduled→Home/Everyday first; empty prominent group dropped + fallback; within-group order preserved) + `prominent_initial_id` (both live states + the empty-prominent fallback).
- `docs/claugentic-ARCHITECTURE_TREE.md` — **add** `nav_rail.py`; update `shell.py`/`nav.py` descriptions.
- `docs/claugentic-DECISIONS.md` — **edit (at Land)**: IA-1 one-liner (option a + why).

## Risks & mitigations
- **~~Custom nav loses `NavigationRail` a11y~~ — RESOLVED by the gate (#1):** IA-1 **retains `ft.NavigationRail`** (option a), so keyboard/focus/selection semantics stay built-in; no custom focusable nav is built (its 0.85.3 a11y was unverified). Section headers / stacked-rails / custom nav are deferred behind a future time-boxed a11y spike only if headers ever become required.
- **Initial-selection change** (prominent group's first dest, not index 0) → covered by a pure `prominent_initial_id` test; **total** — degrades to the first ordered destination when the prominent group is empty (gate #2).
- **Selection-state churn** (re-render highlight) → **native** `selected_index` (gate #4); `ft.NavigationRail` manages its own highlight on click, shell sets only the initial one; select-by-`dest.id` (`screens` is id-keyed) so no index drift.
- **`shell.py` refactor regresses the close lifecycle / setup swap / demo override** → keep those blocks byte-identical; `do_exit`/`_on_leave` **stay in `shell.py`** and pass into `build_nav` as `on_exit` (gate #3); only the rail view moves; the windowed-exe smoke + manual `DISTRICTSYNC_UI=flet` confirm.
- **Coverage** → `ordered_destinations` + `prominent_initial_id` COUNTED; only `nav_rail.py` omitted (matches the shell/components/picker_field/setup view-omit pattern).

## Test strategy
- **Unit (covered):** `ordered_destinations` — prominent-first ordering for each live state (unconfigured → Get-started/`setup` leads; configured+scheduled → Everyday/`home` leads); empty groups dropped; **empty prominent group** handled (contributes nothing, remaining groups lead); intra-group order preserved (full id set is a permutation of the six). `prominent_initial_id` — unconfigured→`setup`, configured+scheduled→`home`, empty-prominent→first ordered destination.
- **Manual (`DISTRICTSYNC_UI=flet`):** unconfigured launch → **Setup leads the rail + is selected**; after saving folders + (faking) `schedule_registered`, relaunch → **Home leads + selected**; selecting any destination highlights it (native) + shows its screen; Exit + window-close still clean. (No headers — flat reordered rail.)
- **Regression:** full `pytest` + SD74 snapshot + ruff/format + mypy(non-UI) + bandit + config-validate green; architect core-untouched (zero `src/etl|config|sftp|scheduler|main.py` diff); the PLAT-3 windowed-exe smoke still opens + zero-orphan closes.

## Decomposition (slices)
**ONE slice** — reorder the flat rail state-aware (render the existing model) + the F6 shell split; complete + tested, no half-state (every destination still reachable; the model/rule unchanged).
- [ ] **IA-1 — shell assembly + state-aware reordered rail** · `nav.ordered_destinations`/`prominent_initial_id` (pure) · `nav_rail.py` (flat rail view from DS-1) · `shell.py` slimmed + id-keyed selection + prominent-initial · tests · tree/decisions. **Lands complete:** the new UI leads a non-technical admin to the right place for their state, with a branded, built-in-a11y reordered rail; `shell.py` F6 boundary split; **no debt** (surface *content* is IA-2/IA-3+, reusing this frame).

---
## Review

`RUNNING AS: Opus 4.x` — *a separate clean-context plan-gate pass on the most capable model; a reduction of rubber-stamping risk, not an independent-model guarantee. This same-model run does not de-correlate blind spots.*

**Verdict: CHANGES REQUIRED** (M/low — proportionate; not gold-plated). The slicing, the pure/view tiering, the F6 split, the `dest.id` re-key, and the prominent-initial-selection are all sound and well-scoped. The block is **one load-bearing design call** (custom Column vs NavigationRail) that the plan picks on an **unverified a11y premise** and against a twice-stated program-level commitment — plus three smaller gaps. The plan even carries the right fallback in its own Risks section; it just hasn't been forced to decide.

### Required changes (numbered, actionable)

1. **Do NOT pre-commit to the custom-`ft.Column` nav. Pick the cheapest option that meets the mandate, and gate the custom path behind an a11y spike. [BLOCKING — the key call]**
   The plan replaces `ft.NavigationRail` with a hand-rolled `ft.Column` of DS-1 buttons to get section headers. But:
   - **The program never mandated section *headers*.** `0013-flet-production-redesign.md:43` and `:59` both say **"state-aware grouped `NavigationRail`"** — naming the *control* and the word "grouped" + "prominent when unconfigured," never "section-header text." The actual user job (this plan's own Goal, line 12) is *"lead the unconfigured admin to Setup, the configured one to Everyday."* **That job is fully satisfied by state-aware ORDERING of a flat rail** (prominent group's destinations first) — no headers, no custom control, no lost semantics.
   - **The a11y premise under the custom path is UNVERIFIED.** The plan asserts a custom nav built from "focusable DS-1 button controls with a visible focus state + min target" meets the 0013 baseline. Nothing in the repo supports that: `components.text_button`/`primary_button` are `ft.TextButton`/`ft.FilledButton` with NO focus styling; there is **zero** `on_focus`/keyboard/focus-state code anywhere in `src/ui_flet` (grep clean); the spike (`docs/reference/flet-prototype-spike/app.py`) never builds a focusable non-button nav item; and `FLET_1.0_CONVENTIONS.md` explicitly warns that focus/keyboard knowledge from training data is for the wrong (0.2x) API. `ft.NavigationRail` gives keyboard traversal, selection semantics, and a focus indicator **for free** on 0.85.3; a custom Column must re-implement all of it, on a beta API, with no proven recipe — that is exactly the kind of unverified claim Risk #1 hand-waves past.
   - **Therefore: order the options by cost and force the decision in the Spec.** Preference order:
     - **(a) Reorder a flat `NavigationRail`** (selected default) — keep the single rail, reorder `destinations` so the prominent group leads; keep all built-in a11y; *no headers*. Cheapest, zero a11y regression. **State-aware ordering is the mandate; headers are gold-plating IA-1 doesn't need.** If the product genuinely needs visible group separation, a non-focusable header `ft.Text` between **stacked NavigationRails** (option b) is the next rung.
     - **(b) Stacked `NavigationRail`-per-group** with header `Text` between (the plan's own noted alternative) — keeps per-destination a11y, gets visible headers, costs a one-selection-across-rails coordinator. Acceptable if headers are deemed required.
     - **(c) Custom `ft.Column`** — ALLOWED ONLY IF a **time-boxed a11y spike first proves** (against installed `flet==0.85.3`, recorded in `FLET_1.0_CONVENTIONS.md` like every other API fact) that a custom nav item is keyboard-focusable with a visible focus state on 0.85.3. Absent that proof, (c) regresses the a11y baseline and is rejected. **The plan must not assume (c) is feasible — it must verify or fall back.**
   This is the genuine design fork in this slice; the Spec must state which option it builds and why, not default to the most expensive one on an unproven assumption.

2. **`ordered_groups` empty-prominent-group behavior is asserted in Risks but not specified or tested. [correctness]**
   Approach (line 21) says "non-empty groups in display order"; Risks (line 43) says "degrade to index 0 if the prominent group is somehow empty." Make this a contract, not a hope: specify that `ordered_groups` **drops empty groups** and that `prominent_initial_id(model)` **falls back to the first destination of the first non-empty ordered group** when the prominent group is empty (today every group is non-empty, but the helper is pure and must be total). Add the empty-prominent-group case to the counted test list (Test strategy currently tests only the two happy states).

3. **The brand-mark + Exit move into `nav_rail.py` must preserve the exact close/exit contract — name it as a constraint, not a comment. [reliability-resilience]**
   `do_exit` calls `_on_leave(page)` then `page.window.destroy()` (shell.py:197-202), and the same teardown is bound on `on_window_event`/`on_disconnect`. Moving the Exit button into `nav_rail` is clean ONLY if `do_exit` (and `_on_leave`) **stay in `shell.py`** and are passed into `build_nav` as the `on_exit` callback — the lifecycle owner must remain the shell. The Spec must state: `nav_rail.build_nav(...)` receives `on_select` and `on_exit` callbacks and owns **no** lifecycle logic; the brand mark is pure presentation. (This keeps Risk #4's "close lifecycle byte-identical" actually true.)

4. **Re-render mechanism for selection highlight must be pinned. [maintainability-structure]**
   Approach (line 23) offers "rebuild `build_nav` or toggle the selected styling" as an either/or. With option 1(a)/1(b) (NavigationRail) this is free (`rail.selected_index = …`). With option 1(c) (custom) it is not — pick ONE mechanism in the Spec (rebuild the nav subtree vs. mutate per-button style + `page.update()`), since the choice is coupled to whichever nav option lands in #1. Leaving it open is a deferred decision inside a "lands complete" slice.

### Sizing / completeness check
- **IA-1 (single slice):** **OK — correctly one session, lands complete.** No half-state (every destination stays reachable; model/rule unchanged; placeholders persist for unbuilt surfaces). The pure/view split is right: `ordered_groups` + `prominent_initial_id` COUNTED in `nav.py` (genuinely pure — ordering a tuple of frozen dataclasses, no flet import), `nav_rail.py` coverage-omitted matching the established `shell/components/picker_field/setup` view-omit pattern in `pyproject.toml`. **One caveat:** if change #1 lands option (a) (reorder a flat rail), the "extract `nav_rail.py`" goal shrinks toward a thin reorder — re-confirm at Spec that a *new module* is still warranted vs. an in-shell reorder + the F6 split done minimally. Don't manufacture a module to justify the split; the F6 split is real, but a 10-line reorder may not need its own file. Keep the slice; right-size the extraction.

### Harness impact
- **No new STANDARD/agent.** Touches `docs/claugentic-ARCHITECTURE_TREE.md` (add `nav_rail.py`; update `shell.py`/`nav.py` lines — already listed) and `docs/claugentic-DECISIONS.md` at Land (already listed). 
- **One addition:** whichever nav option #1 lands is a **non-trivial UI-architecture decision** (esp. if a custom nav + a11y spike) — the DECISIONS line must record *which* of (a)/(b)/(c) and *why*, and if (c), the spike's a11y finding belongs in `FLET_1.0_CONVENTIONS.md` (the authoritative 0.85.3-API-fact home), not just DECISIONS.

### KEPT spine (sound — do not churn)
- Realize the PLAT-1 model as a **render**, no new "configured" logic — correct; reuse `nav_model`, add only pure ordering.
- **`dest.id` selection** replacing the flat index — sound; `screens` is already id-keyed (shell.py:127,172,176), so this decouples render order from the screen map with no index drift. Keep.
- **Prominent-initial-selection** (prominent group's first dest, not index 0) — sound and testable; just make it total (#2).
- **F6 `shell.py` split** at IA-1 — correct point; keep window/content-host/close in shell, nav out (bounded by #3).
- **Zero core touch** (`src/etl|config|sftp|scheduler|main.py`) — architect gate trivially met; keep the regression assertion in Test strategy.

## Spec

> Folds the plan-gate ruling (see `## Review`). **Option (a) — reorder a single flat `ft.NavigationRail`** is what IA-1 builds: no section headers, no custom nav, built-in a11y retained. `nav_rail.py` is reconfirmed warranted as the **F6 rail-view extraction** (a ~60-line view leaves `shell.py`), not a control needed for grouping. The pre-`## Review` sections above were revised to match.

### 1. `src/ui_flet/nav.py` — two pure helpers (COUNTED; no flet import)

Append below `nav_model` (everything else byte-identical):

```python
def ordered_destinations(model: NavModel) -> tuple[Destination, ...]:
    """The flat destination list with the prominent group's destinations FIRST.

    Groups are emitted in display order with ``model.prominent_group`` moved to the
    front; **empty groups are dropped**; within-group order is preserved. Total — an
    empty prominent group simply contributes nothing (the remaining groups then lead
    in canonical order). The ONLY render-ordering decision: ``nav_rail`` builds the
    flat rail from this tuple (option (a); no section headers — see plan 0018 gate).
    """
    lead = model.prominent_group
    order = [lead, *[g for g in NavGroup if g != lead]]
    result: list[Destination] = []
    for group in order:
        result.extend(model.groups.get(group, ()))
    return tuple(result)


def prominent_initial_id(model: NavModel) -> str:
    """The id of the destination to select on launch — the prominent group's FIRST.

    **Total:** if the prominent group is empty, fall back to the first destination of
    ``ordered_destinations(model)`` (the first non-empty ordered group); if there are
    no destinations at all, return ``""``.
    """
    prominent = model.groups.get(model.prominent_group, ())
    if prominent:
        return prominent[0].id
    ordered = ordered_destinations(model)
    return ordered[0].id if ordered else ""
```

`NavGroup` iteration order **is** the canonical display order (`GET_STARTED, EVERYDAY, ADVANCED` — declared in that order).

### 2. `src/ui_flet/nav_rail.py` — new view (coverage-omitted)

A pure view factory: owns **no** lifecycle and reads **no** config (gate #3). `on_select`/`on_exit` are passed in. Build the flat state-aware rail with the brand mark as `leading`, one `ft.NavigationRailDestination` per `ordered` entry, and the Exit affordance in `trailing` via `on_exit`. Move the **brand `leading` block** (the `SYNC_ROUNDED` chip + "District"/"Sync" text) and the **`exit_btn`/`trailing`** verbatim from today's `shell.py`. Contract:

```python
def build_nav(
    *,
    ordered: tuple[nav.Destination, ...],
    selected_id: str,
    on_select: Callable[[str], None],
    on_exit: Callable[..., None],
) -> ft.Control:
    try:
        selected_index = [d.id for d in ordered].index(selected_id)
    except ValueError:
        selected_index = 0

    def on_change(e: ft.ControlEvent) -> None:
        on_select(ordered[e.control.selected_index].id)
    # ... ft.NavigationRail(selected_index=selected_index, ..., on_change=on_change,
    #     leading=<brand block, verbatim>, destinations=[... for d in ordered],
    #     trailing=<Exit via components.text_button("Exit", on_exit, ...)>)
    # return ft.Container(content=rail, bgcolor=tokens.color_surface,
    #     border=ft.Border(right=ft.BorderSide(1, tokens.color_border)))
```

Imports: `import flet as ft`; `from src.ui_flet import components, nav, tokens`. Define locally only the tiny layout helper(s) it needs (`pad`/border) — mirrors `components.py`'s local helpers (avoids a `shell`↔`nav_rail` import cycle). Build the Exit via `components.text_button` (never hand-roll — FilledButton `text=` trap).

### 3. `src/ui_flet/shell.py` — slim to assembly + lifecycle

**Remove:** the inline `rail`/`rail_wrap` (+ its `leading` brand + `exit_btn`/`trailing`), the index-based `render`/`select`/`on_nav_change`, the `destinations = model.destinations` local, and any layout helper the move leaves unused (`pad`, `b_only`, and `b_all` if already unused). Keep `pad_sym` (still used by `content_host` + `build_placeholder`).

**Keep byte-identical:** themed chrome + window sizing, `build_placeholder`/`build_screens`, `_on_leave`, `do_exit`, `on_window_event`, `on_disconnect`, the `functools.partial(build_setup, page)` swap, the `DISTRICTSYNC_UI_DEMO` override.

**New wiring in `main`** (add `nav_rail` to the `from src.ui_flet import …`):

```python
model = nav.nav_model(AppConfig.load())
screens = build_screens(model.destinations)
screens["setup"] = functools.partial(build_setup, page)
if os.environ.get("DISTRICTSYNC_UI_DEMO") and "help" in screens:
    screens["help"] = components.build_design_demo

ordered = nav.ordered_destinations(model)
initial_id = nav.prominent_initial_id(model)
content_host = ft.Container(expand=True, padding=pad_sym(36, 28))

def render_by_id(dest_id: str) -> None:
    inner = screens[dest_id]()
    content_host.content = ft.Column(controls=[inner], scroll=ft.ScrollMode.AUTO, expand=True)

def select_by_id(dest_id: str) -> None:
    render_by_id(dest_id)
    page.update()

def do_exit(_e: ft.ControlEvent | None = None) -> None:   # unchanged body
    _on_leave(page)
    try:
        page.window.destroy()
    except Exception:
        os._exit(0)

nav_view = nav_rail.build_nav(
    ordered=ordered, selected_id=initial_id,
    on_select=select_by_id, on_exit=do_exit,
)
page.add(ft.Row(spacing=0, expand=True, controls=[nav_view, content_host]))
# ... on_window_event / on_disconnect blocks unchanged ...
render_by_id(initial_id)
page.update()
```

Highlight is **native** (gate #4): `ft.NavigationRail` updates its own `selected_index` on click; shell sets only the initial one via `selected_id` and never mutates the rail again (it holds no rail reference — the decoupling).

### 4. `pyproject.toml`
Add `"src/ui_flet/nav_rail.py",` to `[tool.coverage.run] omit` (after `shell.py`).

### 5. `tests/test_ui_flet_nav.py` — add COUNTED cases
Import `ordered_destinations`, `prominent_initial_id`, `Destination`, `NavModel`. Add:
- **unconfigured** (`AppConfig()`): `ordered_destinations(...)[0].group is NavGroup.GET_STARTED` and `[0].id == "setup"`; `prominent_initial_id(...) == "setup"`.
- **configured+scheduled** (`AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", schedule_registered=True)`): `[0].group is NavGroup.EVERYDAY` and `[0].id == "home"`; `prominent_initial_id(...) == "home"`.
- **completeness/order:** `{d.id for d in ordered_destinations(...)} == _EXPECTED_IDS` (permutation, none dropped); within-group order preserved (Everyday stays `home, convert, run_history`).
- **empty prominent group:** hand-build `NavModel(destinations=(home,), groups={GET_STARTED: (), EVERYDAY: (home,), ADVANCED: ()}, prominent_group=GET_STARTED)` → `ordered_destinations` returns `(home,)` (empties dropped), `prominent_initial_id` falls back to `"home"`.

### 6. `docs/claugentic-ARCHITECTURE_TREE.md`
- **Add** a `src/ui_flet/nav_rail.py` line under the ui_flet section (view glue; `build_nav` flat state-aware rail; no lifecycle).
- **Update** `nav.py` (now also `ordered_destinations`/`prominent_initial_id`; "the rail renders flat this slice, prominence wiring is IA-1" → "rendered by `nav_rail` as a reordered flat rail").
- **Update** `shell.py` ("flat `NavigationRail`" → "delegates the rail view to `nav_rail`; owns window + content host + close lifecycle").

### 7. `docs/claugentic-DECISIONS.md` (at Land, by the orchestrator)
IA-1 one-liner: option (a) reorder-flat rail (why: mandate is the control + state-aware ordering, not headers; custom-nav a11y unverified on 0.85.3 → built-in retained), the F6 `nav_rail` split, native highlight, zero core.

### Build discipline (implementer)
- Branch `feat/0018-flet-ia1-shell` (exists, off `feat/flet-ui-rebuild`). **Commit early** once files compile + tests pass.
- Stage ONLY these by explicit path — **never** `git add -A`:
  `src/ui_flet/nav.py  src/ui_flet/nav_rail.py  src/ui_flet/shell.py  pyproject.toml  tests/test_ui_flet_nav.py  docs/claugentic-ARCHITECTURE_TREE.md`
  (DECISIONS is edited at Land by the orchestrator.) Do **not** stage the pre-existing noise (`.claude/settings.json`, `CLAUDE.md`, other `docs/claugentic-*`, `.githooks/`, `docs/claugentic-PLAN_TEMPLATE.md`).
- **Zero** change to `src/etl|config|sftp|scheduler|main.py`.
- Gates before commit: `pytest` full (80% gate) · `ruff check src/ tests/` + `ruff format --check src/ tests/` · `mypy src/ --exclude 'src/ui'` (the pre-existing `classes.py:130` pandas-stubs error is NOT yours — cause no new ones) · `bandit -r src/ -q` · tree-check `python scripts/claugentic-check_architecture_tree.py --staged`.
- All buttons/cards via `components.py`.
