---
name: districtsync-design
description: >-
  DistrictSync desktop UI (Flet) design system — "Branded Professional" (Direction B). Load this
  for ANY change under src/ui_flet/ (screens, components, tokens, theme, shell, nav_rail) so the
  work inherits the design standard. Encodes the non-negotiables (build via components.py factories;
  tokens are the only hex/size source; ONE filled primary per screen; verdict-first layout; toned
  bands not saturated fills; the AA contrast test must stay green; plain-language vocabulary), the
  component inventory + when-to-use, the Flet 0.85.3 gotchas, and the UI-slice definition of done.
  Authority: docs/DESIGN_SYSTEM.md.
---

# DistrictSync design system — enforcement companion

**Authority:** `docs/DESIGN_SYSTEM.md` (the standard) + `src/ui_flet/tokens.py` (the only hex/size
source) + `src/ui_flet/components.py` (the only control factories). This skill is the checklist; the
doc is the reference. On any conflict, the harness + `docs/DESIGN_SYSTEM.md` win.

## Non-negotiables (do these every time)
- **Build via `components.py` factories.** Never hand-roll an `ft.FilledButton` / card / band / chip
  in a screen. Need a shape the inventory lacks? Add the factory to `components.py` (+ a token if it
  needs a new hex/size), then use it. The screen is assembly, not styling.
- **Tokens are the ONLY hex/size source.** No inline `#RRGGBB`, no bare px number, in a screen OR in
  a factory call. Add `space_*` / `radius_*` / `type_*` / `color_*` to `tokens.py` first.
- **ONE filled primary per screen.** Exactly one `primary_button` (the main action). Everything else
  is `secondary_button` (OUTLINED) or `text_button`. Two filled primaries = a bug.
- **Verdict-first layout.** Lead the screen with `page_header`, then the `HealthVerdictBanner`
  (verdict band), then the detail. The band answers "did the roster sync?" before any metric.
- **Toned bands, not saturated fills.** Status backgrounds are the calm tints (`*_tint` + `*_line`)
  with deep on-tint text; the saturated `color_status_*` hue appears ONLY as the small solid icon
  disc. Never white-on-saturated for body copy.
- **Colour is semantic-only + never colour-alone.** Green/amber/red = healthy/attention/failed; each
  band/pill also carries an icon + plain words.
- **Plain-language vocabulary map:** SFTP → "Delivery to SpacesEDU" · GDE → "MyEd BC extract files" ·
  config/sis_type → "district" · rostering entities → Students/Staff/Families/Classes/Enrollments.
- **The AA contrast test must stay green.** Any painted fg/bg pair goes in `tokens.UI_CONTRAST_PAIRS`
  and must clear ≥ 4.5:1 (`tests/test_ui_flet_tokens.py`). Never delete a pair to pass.
- **Gradient is onboarding-only.** `hero_gradient()` is reserved for the first-run onboarding hero;
  every other screen leads with `page_header` (no gradient hero).

## Component inventory — when to use (`src/ui_flet/components.py`)
- `page_header(title, subtitle=None, trailing=None)` — slim white/transparent top block. `trailing` =
  a `district_chip` or the screen's one action. Replaces the old gradient heroes.
- `HealthVerdictBanner(verdict, headline=, detail=, trailing=None)` — the verdict band. `trailing` =
  the ONE fix action (a `primary_button`) or a link (`text_button`).
- `metric_tile(label, value)` — a calm tile (navy `type_metric` numeral + uppercased muted caption).
- `primary_button(...)` — the single filled action (hover/pressed/focus states; `disabled_bgcolor`
  for a gated fill). `secondary_button(...)` — outlined supporting action. `text_button(...)` —
  tertiary/dismiss (MB_PRIMARY text; `color` overridable on a coloured ground).
- `card(content, gradient=None, ...)` — white `radius_lg` surface + 1dp shadow (gradient = hero).
- `district_chip(label)` — rounded district-identity pill. `status_pill(label, status)` — compact
  toned status marker (`status` is a `Verdict`).
- `FileChip` · `run_table` · `ErrorCard` — file chip · run-history table · never-crash error surface.

## Flet 0.85.3 gotchas (full list: `docs/FLET_1.0_CONVENTIONS.md` — READ FIRST)
- Button label is `content`, NOT `text` (`FilledButton(text=)` raises). `OutlinedButton` mirrors
  `FilledButton` (`content`/`icon`/`style`; per-state `side`/`overlay_color` maps).
- `ft.Dropdown` value-change is `on_select`, NOT `on_change` (a static gate bans the wrong form).
- `ft.TextField` helper field is `helper` (+ `hint_text`), NOT `helper_text`.
- Padding/border via `ft.Padding` / `ft.Border` / `ft.BorderSide` dataclasses (0.2x `ft.padding.*` /
  `ft.border.*` helpers are gone). Card shadow = `ft.Container(shadow=ft.BoxShadow(blur_radius=,
  offset=ft.Offset(0,1), color=ft.Colors.with_opacity(0.05, MB_TEXT)))`.
- No `font-variant-numeric` on 0.85.3 — tabular numerals are approximated via `TextStyle(letter_spacing=)`.
- `page.window.destroy()/.close()/.center()` are async (a bare sync call silently no-ops).

## Definition of done for a UI slice
1. **Render smoke green** — `tests/test_ui_flet_render_smoke.py` (every screen constructs on 0.85.3,
   no fall to `ErrorCard`). New component form? Add a mount-smoke.
2. **Contrast green** — `tests/test_ui_flet_tokens.py` (every `UI_CONTRAST_PAIRS` entry ≥ 4.5:1).
3. **Conventions clean** — no `ft.Dropdown(on_change=)`, no `FilledButton(text=)`, no inline
   hex/px in screens; built via `components.py`.
4. **One-primary + verdict-first** upheld on every touched screen; copy uses the vocabulary map.
5. Record any newly-verified Flet API form in `docs/FLET_1.0_CONVENTIONS.md`; a non-trivial design
   decision in `docs/claugentic-DECISIONS.md`.
