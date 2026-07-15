# DistrictSync Design System — "Branded Professional" (Direction B)

> **AUTHORITATIVE for the DistrictSync desktop UI (`src/ui_flet/`).** The design system lives as
> CODE (`src/ui_flet/tokens.py` = the only hex/size source · `src/ui_flet/components.py` = the only
> control factories) + this standard + the `districtsync-design` skill. Screens **never** hand-roll a
> style — they call the factories, which size against the tokens. The visual contract is the
> owner-approved mockup (Direction B, chosen 2026-07-15 from a 3-direction panel). Adopting the
> factories across the screens is Slice 2 of plan `0033`; Slice 1 ships the system + restyles every
> factory globally.

## Identity
The myBlueprint **navy owns the navigation rail**; the content stays white and quiet on a soft wash.
One brand-blue **filled** action per screen, a **toned verdict band** directly under a slim page
header, **navy tabular numerals** on calm metric tiles. Every element maps 1:1 onto Flet 0.85.3
Material primitives — solid fills, 1px borders, standard radii, 1–2dp shadows.

Two marks: the **myBlueprint "m" mark is the running window / title-bar / taskbar icon**
(`assets/myblueprint.ico`, sourced from the official myB favicon — transparent 16/32/48 layers,
resolved via `paths.window_icon_path()`); the **DistrictSync sync mark stays the EXE file icon**
(`assets/districtsync.ico`, baked by `flet pack --icon`) and the **sync glyph is the in-app rail
brand mark** (it signifies DistrictSync — "roster sync for SpacesEDU").

## Principles
1. **Verdict-first.** The health band ("did the roster sync?") is the top content element — a
   plain-language answer before any metric.
2. **ONE filled primary per screen.** Exactly one `primary_button` carries the screen's main action;
   every other action is a `secondary_button` (outlined) or `text_button` (tertiary). This is *why*
   Direction B inverted the old filled-navy secondary to outlined.
3. **Colour is semantic-only.** Green / amber / red mean healthy / attention / failed and nothing
   else; the rail navy and brand blue are chrome/action, never decoration. Status is **never
   colour-alone** — every band/pill also carries an icon + plain words.
4. **Plain language.** Admin-facing copy uses the vocabulary map, never jargon: SFTP → "Delivery to
   SpacesEDU", GDE → "MyEd BC extract files", "config" → "district".
5. **Every failure routes to a fix.** A warning/failed band names the fault and offers the concrete
   fix as the screen's single filled action (e.g. "Open Setup").
6. **Honesty.** A band asserts only **verified** facts (e.g. "delivered" only after a confirmed
   upload) — the trust architecture the UI was built on.

## Tokens — single source: `src/ui_flet/tokens.py`
Never inline a hex or a px size in a screen or a factory arg; add a token, then reference it.

### Palette (hex · role)
| Token | Hex | Role |
|---|---|---|
| `MB_PRIMARY` / `color_action_primary` | `#1D5BB5` | the ONE filled action |
| `color_action_primary_hover` | `#174A96` | filled-primary hover / focus |
| `MB_DARK` / `color_action_primary_strong` | `#0F2D6B` | nav rail bg · metric numerals · primary pressed |
| `MB_ACCENT` / `color_rail_active_accent` | `#0EA5E9` | rail active accent bar |
| `color_action_outline` | `#A9C3E8` | secondary (outlined) button border |
| `color_content_wash` | `#F7F9FC` | content-area wash (white cards float on it) |
| `color_chip_bg` / `MB_LIGHT_BG` | `#F0F6FF` | chip / pill fill |
| `MB_BORDER` / `color_border` | `#DBEAFE` | card / hairline border |
| `color_text` / `MB_TEXT` | `#0F172A` | body text |
| `color_muted` | `#475569` | muted captions (AA-safe on white AND wash) |
| `color_rail_text` / `color_rail_text_active` | `#BFCBE4` / `#FFFFFF` | rail label at rest / active |
| `color_status_healthy` · `_warning` · `_failed` | `#15803D` · `#B45309` · `#DC2626` | solid verdict **icon-disc** fills |
| healthy tint / line / on-tint | `#E8F3EC` / `#BFDCCB` / `#14532D` | healthy band + pill |
| warning tint / line / on-tint | `#F9F0E2` / `#E7CDA8` / `#7A3E07` | warning band + pill |
| failed tint / line / on-tint | `#FDECEC` / `#F3C1C1` / `#7F1D1D` | failed band + pill |

### Spacing (`space_*`, px): 4 · 8 · 12 · 16 · 24 · 32 &nbsp;·&nbsp; Radius (`radius_*`): sm 8 · md 10 · lg 12
### Type ramp (`type_*`, px): caption 12 · body 13 · emphasis 14 · section 16 · title 20 · metric 26

## Components — inventory & usage (`src/ui_flet/components.py`)
| Factory | Use for | Rules |
|---|---|---|
| `page_header(title, subtitle=None, trailing=None)` | slim top-of-screen block | white/transparent, NO gradient/card; `trailing` = a `district_chip` or one action |
| `HealthVerdictBanner(verdict, headline=, detail=, trailing=None)` | the **verdict band** | toned tint + 1px line + solid icon disc + deep on-tint text; `trailing` = the ONE fix action / a link |
| `metric_tile(label, value)` | a count / status tile | navy `type_metric` numeral over an uppercased muted `type_caption` label |
| `primary_button(...)` | the **one** filled action | max ONE per screen; `disabled_bgcolor` carries a gated fill |
| `secondary_button(...)` | supporting actions | **outlined** (white bg, soft-blue border, blue text) — many allowed |
| `text_button(...)` | tertiary / dismiss | MB_PRIMARY text; `color` overridable for coloured grounds |
| `card(content, gradient=None, ...)` | a surface | white `radius_lg` + 1dp shadow; `gradient` = onboarding hero only |
| `district_chip(label)` | district identity | rounded `color_chip_bg` pill in the header right-slot |
| `status_pill(label, status)` | a compact status marker | toned per `Verdict`; icon + text (never colour-alone) |
| `FileChip` · `run_table` · `ErrorCard` | file chip · run table · never-crash error surface | unchanged intent |

**The one-primary rule is a review gate:** a screen with two `primary_button`s is a bug — demote the
weaker one to `secondary_button`. Gradient (`hero_gradient`) is reserved for the first-run onboarding
hero; every other screen leads with `page_header`.

## Accessibility
Every foreground/background pair the UI paints is enumerated in `tokens.UI_CONTRAST_PAIRS` and the
tokens test (`tests/test_ui_flet_tokens.py::test_every_ui_pair_clears_wcag_aa`) asserts each clears
**WCAG AA (≥ 4.5:1)** at authoring time — a palette tweak that breaks legibility fails the build, not
a user's eyes. Every verdict band/pill carries an icon **and** words, so a status is never
communicated by colour alone. Direction B pairs all clear comfortably (deep-on-tint 7.4–8.8:1, rail
label 8.0:1, navy numerals on white 13.1:1).

## Flet 0.85.3 mapping (see `docs/FLET_1.0_CONVENTIONS.md` — READ FIRST)
- Buttons: `primary` → `ft.FilledButton` (label = `content`, per-state `bgcolor` map);
  `secondary` → `ft.OutlinedButton` (per-state `side`/`overlay_color` maps); `text` → `ft.TextButton`.
- Band/tile/chip/pill → `ft.Container` (`bgcolor` + `ft.Border` + `border_radius` + `ft.BoxShadow`),
  the disc a fixed-size circular `Container` holding an `ft.Icon`.
- Padding/border via the dataclasses `ft.Padding` / `ft.Border` / `ft.BorderSide` (the 0.2x
  `ft.padding.*` / `ft.border.*` helpers are gone). `ft.TextStyle(letter_spacing=)` for caps tracking
  (no `font-variant-numeric` on 0.85.3 — tabular numerals are approximated via tight tracking).

## How to build UI here
1. **Build via `components.py` factories** — never hand-roll a control. If a screen needs a shape the
   inventory lacks, add the factory here (and a token if it needs a new hex/size), then use it.
2. **Tokens are the only hex/size source** — no inline `#RRGGBB` or px literal in a screen or a
   factory call. Adding a style = add a token + (if needed) a factory, first.
3. **One filled primary per screen; lead with the verdict band; use toned bands, not saturated fills.**
4. **Definition of done for a UI slice:** render-smoke green (`tests/test_ui_flet_render_smoke.py`) +
   the contrast test green + no new `ft.Dropdown(on_change=)` / `FilledButton(text=)` / inline styles.
   The `districtsync-design` skill (`.claude/skills/districtsync-design/`) is the enforcement
   companion — it loads on any `src/ui_flet` change and points back here.
