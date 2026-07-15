# 0033 — DistrictSync Design System (Direction B "Branded Professional") + UI adoption

- **Status:** Approved by owner 2026-07-15 ("i like B please proceed") — implementing
- **Branch:** `feat/0033-design-system` stacked on `fix/0031-sftp-trust-gaps` (PR #53) — owner merges in order.
- **References:** `.claude/plans/0032-ui-ux-sweep-proposal.md` (Tier 2 #5/#6 are this plan's core) · the approved mockup `scratchpad/mockups/branded-professional.html` (visual spec, published as artifact) · `src/ui_flet/tokens.py` (foundation) · `docs/FLET_1.0_CONVENTIONS.md`
- **Owner decisions:** Direction B; title-bar/window logo → myBlueprint logo (ASSET PENDING from owner — interim: keep `districtsync.ico`); in-app/rail brand mark → the sync glyph (signifies DistrictSync); design system formalized in-repo (doc + project skill) so future work inherits it.

## Thesis
Keep the trust architecture; make the visual hierarchy commercial-grade per the approved Direction B mockup: navy owns the nav rail, content stays white/quiet, ONE brand-blue filled action per screen, a toned verdict band directly under a slim page header, navy tabular numerals on calm tiles. The design system lives as CODE (tokens + component factories) + a one-page standard + a project skill — screens inherit; they never hand-roll styles.

## Slice 1 — the system (tokens + components + doc + skill)
1. **`src/ui_flet/tokens.py`** (extend, never break): spacing scale (4/8/12/16/24/32), radius scale (sm 8 / md 10 / lg 12), type ramp (caption 11–12 / body 13 / emphasis 13.5 / section 15.5–17 / title 20 / metric 26), Direction B roles: `color_rail_bg=MB_DARK`, `color_rail_text` (white @ .78), `color_rail_active_accent=MB_ACCENT`, tinted status surfaces (`color_status_healthy_tint #E8F3EC` + line `#BFDCCB`, warning `#F9F0E2`/`#E7CDA8`, failed tint), `color_content_wash #F7F9FC`, `color_chip_bg=MB_LIGHT_BG`, deep on-tint text (`#14532D` green-deep, `#7A3E07` amber-deep). New `UI_CONTRAST_PAIRS` entries for every new fg/bg pairing (on-tint text, rail text on navy, navy numerals on white).
2. **`src/ui_flet/components.py`**: `page_header()` (slim white header: title + sub + right-slot — replaces gradient heroes), `verdict_band()`/inline variant (toned band, solid icon disc, deep on-tint text, optional trailing action), `metric_tile()` restyle (26px navy tabular numerals, caps muted label, MB_BORDER card + 1dp shadow), 3-tier buttons through the existing single seam (primary filled blue — ONE per screen; secondary → OUTLINED; tertiary text), `district_chip()`, `status_pill()`. Hover/pressed/focus states on buttons/fields (0032 Tier-1 #10 subset).
3. **`docs/DESIGN_SYSTEM.md`** — the formal standard: principles (verdict-first, one-primary, semantic-color-only, plain language), token tables, component inventory + usage rules, do/don't, Flet mapping notes. Referenced from CLAUDE.md.
4. **`.claude/skills/districtsync-design/SKILL.md`** — project skill (committed): triggers on any UI work; encodes the standard + Flet 0.85.3 gotchas + "build via components.py, never inline styles".
5. Tests: token contrast (new pairs), existing component gate patterns extended; full suite green.

## Slice 2 — chrome + screens adoption
1. **`shell.py`/`nav_rail.py`**: navy rail (Direction B: white icons/labels @ .78, active = 12% white pill + 3px sky accent bar, brand block = sync glyph + "DistrictSync"/"Roster sync for SpacesEDU", divider, reassurance line, Exit, `v{app_version()}` caption), content area on `color_content_wash`, content width cap ~960px (0032 Tier-1 #5).
2. **Screens sweep** (`home`, `convert`, `run_history`, `setup` incl. wizard steps + Settings, `mapping`, `help`, `onboarding`): gradient heroes → `page_header()` (gradient reserved for the first-run onboarding hero ONLY); Home verdict-first (banner top element, greeting → header sub, district chip right); Convert per mockup (form card, chips, one primary, post-run verdict band + secondary actions); other screens adopt header + band + tiles idioms. No copy changes beyond what the components require (the 0032 vocabulary sweep is a SEPARATE slice — don't creep).
3. Window icon: keep `districtsync.ico` until the owner supplies the official myBlueprint logo (then: window/taskbar icon → myB, rail mark stays sync glyph). Recorded as pending-asset.
4. Update `tests/test_ui_flet_render_smoke.py` + any token/component tests; conventions doc entries for new component forms.

## Non-goals (explicitly deferred to 0032 backlog)
Copy/vocabulary sweep · Mapping-Apply reconcile + deliver-ack trust bugs (Tier 2 #1 / Tier 1 #3 — separate slices) · window geometry persistence · dark mode (declined by both visual lenses) · alerting.

## Verification
Full suite + ruff + mypy(non-UI) + bandit + validate-config + tree-check green per slice; render-smoke green; contrast test proves every painted pair ≥ AA. Visual QA: run the app, compare against the approved mockup.
