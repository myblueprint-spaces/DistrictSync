<!-- claugentic-dev-harness@0.1.40 managed — do not edit (copied from the claugentic-dev-harness plugin) -->
---
# ── Module contract (copied from _TEMPLATE.md) ──
module: product-ux
title: Product & UX
version: 0.1.0
status: draft
iso_25010: [interaction-capability]
load_scope:
  keywords: [ui, ux, component, page, screen, design, frontend, button, form, layout, accessibility, a11y, responsive]
  globs: ["**/components/**", "**/pages/**", "**/*.tsx", "**/*.jsx", "**/*.vue", "**/*.css"]
last_reviewed: 2026-06-04
---

# Product & UX — is this a complete, humane, accessible product surface (not just working code)?

> **Loads when:** a change touches a user-facing surface — a component, page, screen, form, layout, styling, or any interactive frontend.
> **ISO/IEC 25010:** Interaction Capability · **Status:** draft · **v0.1.0**

Each entry below is one **auditable dimension**. Per change, the reviewer applies the
*relevant* ones **fully** (select-don't-skip), right-sized to the change — never
gold-plating an irrelevant one, never skipping a relevant one. This module is the
**product/UX lens**: it asks not "does it run?" but "is it a finished, usable, ethical
experience?" — and it ends with the **objective UX signals** a `ux-reviewer` measures so
judgment is backed by numbers wherever possible.

---

## Information architecture & navigation

- **Good looks like —** content/features grouped by the user's mental model, not the org chart or DB schema; predictable, consistent navigation; the user always knows where they are (breadcrumbs/active states), how they got there, and how to get back; depth kept shallow (≤3 clicks to primary tasks); labels use the user's vocabulary, not internal jargon.
- **Auditor checks —** `[J]` is the nav model consistent across screens (same affordances in the same places)? `[J]` does every screen answer "where am I / where can I go"? `[J]` do labels match user language (card-sort/tree-test evidence if available)? `[D]` are route names/links resolvable (no orphan routes, no broken internal links — link-check in CI)?
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Good IA means people find things without thinking; getting it wrong means users feel lost and abandon tasks even when every feature technically works. It costs upfront structuring effort and the discipline to resist dumping features wherever is convenient.
- **Sources —** Nielsen-Norman Group, *The Difference Between Information Architecture (IA) and Navigation* (nngroup.com/articles/ia-vs-navigation/); *Navigation: You Are Here* (nngroup.com/articles/navigation-you-are-here/); 10 Usability Heuristics #1 (visibility of system status), #2 (match system & real world), #6 (recognition over recall) (nngroup.com/articles/ten-usability-heuristics/).

## Design system & design tokens (single source of truth for look-and-feel)

- **Good looks like —** all visual values (color, spacing, type, radius, shadow, motion) come from **named tokens**, not hardcoded literals; tokens follow a tiered architecture — **global/primitive** (`blue-500`) → **alias/semantic** (`color-bg-interactive`) → **component** (`button-bg-primary`) — so a brand change edits one semantic token and cascades everywhere; components are reused from a shared library before new ones are written; one canonical component per concept (no three subtly different buttons).
- **Auditor checks —** `[D]` grep the diff for raw hex/rgb/px literals in component/CSS files where a token exists (lint rule: `no-hardcoded-design-values` / Stylelint custom rule) `[J]` are semantic tokens used over primitives at the component layer (intent, not raw value)? `[J]` was an existing component reused, or a near-duplicate created? `[J]` is the new component consistent with siblings (states, sizes, spacing scale)?
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Tokens make the whole product re-skinnable and visually consistent from one place; skipping them scatters colors and spacing across hundreds of files so a rebrand becomes a multi-week hunt and the UI drifts out of sync. The cost is the setup of the token layer and the habit of never typing a raw value.
- **Sources —** W3C Design Tokens Community Group, *Design Tokens Format Module* (designtokens.org; W3C CG: w3.org/community/design-tokens/); Material Design 3 *Design tokens* (m3.material.io/foundations/design-tokens).

## Loading / empty / error states (every async surface has all three)

- **Good looks like —** **every** surface that fetches, mutates, or computes asynchronously explicitly handles **all three** non-happy states: **loading** (skeleton/spinner with layout reserved — no jank), **empty** (a helpful zero-state that explains what goes here and a next action, not a blank void), and **error** (a human-readable message + a recovery path — retry/back/contact, never a raw stack trace or silent failure). First-use empty differs from filtered-to-empty.
- **Auditor checks —** `[J]` for each async call in the diff, do all three states render? `[D]` does any data-fetching component lack a loading/error branch (lint rule / data-layer convention — e.g. query hooks forced to handle `isLoading`/`isError`)? `[J]` is the empty state actionable (CTA), not a dead end? `[J]` are error messages specific and recoverable (no swallowed errors, no `[object Object]`)?
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Handling all three states is the difference between a product that feels solid and one that flashes blank screens or freezes on the first network hiccup; it roughly doubles the UI work per screen but is exactly where "looks like a demo" becomes "ready for real users."
- **Sources —** Nielsen-Norman, *Designing Empty States in Complex Applications: 3 Guidelines* (nngroup.com/articles/empty-state-interface-design/) & Heuristic #9 (help users recognize, diagnose, recover from errors); NNG, *Skeleton Screens 101* (nngroup.com/articles/skeleton-screens/).

## Optimistic UI & rollback

- **Good looks like —** for high-frequency, low-risk mutations (like, toggle, reorder, add-to-list) the UI updates **immediately** on intent, then reconciles with the server; on failure it **rolls back cleanly** to the prior state and surfaces a non-blocking error; the optimistic update and its rollback are paired (no orphaned optimistic state); destructive or money-moving actions are **not** optimistic — they confirm first.
- **Auditor checks —** `[J]` does the mutation update local state before the server responds where latency would otherwise hurt? `[J]` is there an explicit rollback on error (cache revert / previous-value restore)? `[J]` are destructive/irreversible actions excluded from optimism (confirmed/pessimistic instead)? `[D]` for libraries with built-in optimistic APIs (e.g. TanStack Query `onMutate`/`onError` rollback), is the rollback handler present (pattern lint/review)?
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Optimistic UI makes an app feel instant instead of laggy on every tap; done wrong it shows users a success that didn't happen and leaves stale state, so it must always pair with a clean rollback. The cost is extra reconciliation logic and careful choice of which actions deserve it.
- **Sources —** TanStack Query *Optimistic Updates* docs — `onMutate` snapshot / `onError` rollback pattern (tanstack.com/query/latest/docs/framework/react/guides/optimistic-updates).

## Perceived performance & micro-interactions

- **Good looks like —** the UI respects the three response-time thresholds — **<0.1s** feels instant (direct manipulation), **<1s** keeps flow (show nothing or a subtle cue), **>1s but <10s** needs a determinate progress indicator; perceived speed is engineered (skeletons, optimistic UI, prefetch on hover/intent, instant feedback on every interaction); micro-interactions (hover, press, focus, state transitions) give immediate, proportional feedback and use motion to clarify cause→effect, never as decoration that delays the user; animation respects `prefers-reduced-motion`.
- **Auditor checks —** `[J]` does every actionable element give instant visual feedback on interaction? `[J]` do operations >1s show progress (and >10s allow cancel)? `[D]` is `prefers-reduced-motion` honored (media-query present where animation is used — grep/lint)? `[J]` is motion meaningful (orienting/causal) and short (≈150–300ms), not gratuitous?
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Perceived performance is what users actually feel — an app can be "fast" on paper yet feel sluggish, or be honestly slow yet feel responsive with the right cues. Investing here buys trust and lower abandonment; over-animating costs attention and can make the product feel slower.
- **Sources —** NNG, *Response Times: The 3 Important Limits* (Nielsen, 0.1/1/10s — nngroup.com/articles/response-times-3-important-limits/); web.dev *Web Vitals* (perceived-performance metrics — web.dev/articles/vitals).

## Visual hierarchy, consistency & brand (look-and-feel)

- **Good looks like —** the most important action/information on each screen is visually dominant (size, weight, color, position, whitespace) and the eye is guided in priority order; one primary CTA per view; spacing, type scale, and color usage are consistent (driven by tokens, see above); the surface is on-brand — voice, imagery, and visual language match the product's identity and feel intentional, not template-default; alignment and rhythm are tidy (grid-based, no arbitrary offsets).
- **Auditor checks —** `[J]` is there a single clear primary action and a sensible scan order? `[D]` raw design-value lint: see *Design system & tokens* dimension — the shared Stylelint `no-hardcoded-design-values` gate covers this check. `[J]` are spacing/type/color choices drawn from the scale with intent (semantic tokens, not just literals that happen to match)? `[J]` does it match the established brand/voice and sibling screens? `[J]` are alignment and grouping clean (Gestalt proximity/similarity), with adequate whitespace?
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Strong hierarchy lets users grasp a screen in a glance and act correctly; a flat, inconsistent layout makes everything compete for attention and the product feel cheap. The cost is design care and saying no to "just one more button up top."
- **Sources —** NNG, Heuristic #4 (consistency & standards) & #8 (aesthetic & minimalist design) (nngroup.com/articles/ten-usability-heuristics/); NNG, *The Principle of Closure in Visual Design* — Gestalt principles of grouping (proximity, similarity, common regions, closure) (nngroup.com/articles/principle-closure/).

## Ethical engagement (habit-forming without dark patterns)

- **Good looks like —** the design earns engagement by delivering real value (clear triggers, easy actions, honest variable reward, genuine investment) — **never** by deception or coercion; categorically **no dark patterns**: no forced continuity / hard-to-cancel, no sneaking items or hidden fees into carts, no confirm-shaming ("No, I don't want to save money"), no fake urgency/scarcity, no preselected opt-ins for data sharing, no obstruction or nagging; the easy path is the honest path; opt-out is as easy as opt-in; defaults serve the user, not just the metric.
- **Auditor checks —** `[J]` does any flow rely on tricking, shaming, or trapping the user to hit a metric? `[J]` is cancel/unsubscribe/opt-out as discoverable and easy as the opposite action? `[J]` are urgency/scarcity/social-proof cues truthful? `[J]` are consent defaults user-favorable (no pre-ticked data sharing)? `[D]` cookie/consent flows meet "reject as easy as accept" where regulated (consent-mode/CMP config check).
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Ethical design builds durable trust and keeps you clear of regulators (FTC/GDPR enforce against dark patterns); manipulative tricks can spike a metric short-term but corrode trust, invite churn, reviews damage, and fines. The cost is occasionally a lower vanity number in exchange for a defensible, loyal user base.
- **Sources —** Brignull, *Deceptive Patterns* taxonomy (deceptive.design); FTC, *Bringing Dark Patterns to Light* (ftc.gov/reports/dark-patterns, 2022); Nir Eyal *Hooked* model — applied **only** with the ethical "regret test."

## User-flow completeness (no dead ends)

- **Good looks like —** every flow has a complete, reversible path: clear entry, a defined success state with an obvious next step, and a defined exit/cancel at each stage; there are **no dead ends** (every screen offers a forward and a backward action); back/cancel/undo behave predictably; partially-completed flows are recoverable (draft/resume) rather than lost; success and failure both land the user somewhere sensible, not on a blank or terminal screen.
- **Auditor checks —** `[J]` trace the new/changed flow end-to-end: is there a way forward AND a way out from every state? `[J]` is the success state explicit with a next action (not just a silent close)? `[J]` is cancel/back non-destructive and predictable? `[J]` can an interrupted flow be resumed (no silent data loss)? `[D]` do all CTA targets resolve (no links/buttons to nowhere — see *Information architecture* dimension for the link/route check gate)?
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** A complete flow means users can always finish, escape, or recover — dead ends are where people rage-quit and trust evaporates. The cost is mapping and building the unglamorous exit/empty/cancel paths, not just the golden path.
- **Sources —** NNG, *10 Usability Heuristics for User Interface Design* — Heuristic #3 (user control & freedom: emergency exits, undo/redo) & #1 (visibility of system status) (nngroup.com/articles/ten-usability-heuristics/).

## Edge-case & resilient UX (offline / slow / flaky network)

- **Good looks like —** the UI degrades gracefully on poor conditions: slow networks show progress and stay interactive (no frozen UI); offline/connection-loss is detected and communicated, with queue-and-retry or a clear read-only/limited mode rather than a hard crash; long content lists virtualize/paginate; extreme inputs (very long strings, huge numbers, empty/zero, RTL, long translations) don't break layout (text truncates/wraps, containers flex); timeouts are bounded with a retry affordance; double-submit is prevented (disabled/idempotent on in-flight).
- **Auditor checks —** `[J]` is there an offline/connection-loss path (detect + message + recover), or is it assumed always-online? `[D]` is double-submit prevented (button disabled / request de-duped while pending — review/lint)? `[J]` does the layout survive extreme content (overflow handled, no clipping/overlap)? `[J]` are long lists windowed/paginated (no unbounded DOM)? `[J]` are network timeouts bounded with user-visible retry?
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Real users are on subways, hotel wifi, and old phones — handling slow/offline/extreme cases is what separates a product that survives the real world from one that only works in the demo. The cost is the extra branches and testing for conditions that "usually" don't happen but always eventually do.
- **Sources —** web.dev, *Offline UX design guidelines* (web.dev/articles/offline-ux-design-guidelines); web.dev, *Resilience* — reliable loading under poor network conditions (web.dev/explore/reliable); NNG, Heuristic #5 (error prevention) (nngroup.com/articles/ten-usability-heuristics/).

## Accessibility (WCAG 2.2 AA — keyboard, contrast, screen reader, focus)

- **Good looks like —** conformance to **WCAG 2.2 Level AA**: full **keyboard operability** (every interactive element reachable and usable without a mouse, logical tab/focus **order**, visible focus indicator, no keyboard traps); **color contrast** ≥ 4.5:1 for normal text, ≥ 3:1 for large text and UI components/graphics; **screen-reader** support via semantic HTML / correct ARIA (labels, roles, names, live regions for async updates), images have meaningful `alt`; meaning never conveyed by color alone; forms have programmatic labels and clear error identification; new 2.2 AA criteria met — **focus not obscured** (2.4.11), **focus appearance** (2.4.13), **dragging alternative** (2.5.7), **target size ≥ 24×24 CSS px** (2.5.8), **consistent help** (3.2.6), **redundant entry** avoided (3.3.7), **accessible authentication** (3.3.8); honors `prefers-reduced-motion`.
- **Auditor checks —** `[D]` automated axe-core / Lighthouse a11y scan passes with zero violations in changed views (CI gate — catches ~30–40% of issues) `[D]` contrast ratios meet AA (token/contrast linter or axe) `[D]` images/icons have alt/accessible names; form controls have associated labels (axe/eslint-plugin-jsx-a11y) `[J]` keyboard-only walkthrough: tab order logical, focus visible & not obscured, no traps, all actions reachable `[J]` screen-reader pass (NVDA/VoiceOver): names/roles/states announced, async changes announced via live regions `[D]` interactive targets ≥ 24×24px (computed-size check) `[J]` no information conveyed by color alone.
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Accessibility makes the product usable by everyone (including the ~15% with disabilities and anyone on a keyboard or assistive tech) and is a legal requirement in many markets (ADA/EN 301 549/European Accessibility Act); skipping it excludes users and invites lawsuits. Most cost is paid by using semantic HTML and tokens from the start — retrofitting later is far more expensive.
- **Sources —** W3C *WCAG 2.2* (w3.org/TR/WCAG22) incl. SC 2.4.11/2.4.13/2.5.7/2.5.8/3.2.6/3.3.7/3.3.8; W3C *What's New in WCAG 2.2* (w3.org/WAI/standards-guidelines/wcag/new-in-22/); Deque axe-core (deque.com/axe).

## Responsive & cross-device/-browser

- **Good looks like —** layout works fluidly from small mobile to large desktop using a mobile-first, fluid approach (relative units, flex/grid, container/media queries) — not fixed-pixel layouts that break between breakpoints; no horizontal scroll at standard widths; touch targets and spacing adapt for touch vs pointer; content reflows (WCAG 1.4.10 — usable at 320px width / 400% zoom with no loss of content/function); verified across the project's supported browser/device matrix; text remains readable (≥16px body on mobile, respects user zoom).
- **Auditor checks —** `[J]` does it hold at key widths (≈320, 768, 1024, 1440) with no overflow/overlap/clipping? `[D]` content reflows at 320px / 400% zoom without horizontal scroll (Lighthouse/manual zoom check — WCAG 1.4.10) `[J]` are touch targets and hit areas adequate on touch devices? `[J]` tested on the supported browser matrix (no engine-specific breakage)? `[D]` viewport meta present and zoom not disabled (`user-scalable=no` forbidden — grep).
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Most traffic is mobile — a responsive UI meets users on whatever screen they have, while a desktop-only layout alienates the majority and fails zoom/accessibility checks. The cost is designing and testing across sizes instead of one, ideally caught by visual-regression snapshots.
- **Sources —** W3C WCAG 2.2 SC 1.4.10 (Reflow) & 1.4.4 (Resize Text) (w3.org/TR/WCAG22/#reflow); MDN, *Responsive design* (developer.mozilla.org/en-US/docs/Learn_web_development/Core/CSS_layout/Responsive_Design); web.dev, *Responsive web design basics* (web.dev/articles/responsive-web-design-basics).

## Objective UX signals (what a ux-reviewer measures)

- **Good looks like —** subjective UX claims are backed by **objective signals** the reviewer can cite, gathered with real tools rather than vibes: **Lighthouse Performance ≥ 90** and **Accessibility ≥ 90** on changed routes; **Core Web Vitals** in the "good" band at field p75 — **LCP < 2.5s**, **INP < 200ms**, **CLS < 0.1** (lab proxy acceptable pre-launch, field data confirms post-launch); **axe-core: 0 violations** on changed views (see *Accessibility* dimension for the axe-core gate); a structured **Nielsen 10-heuristic critique** of the changed flow (each heuristic rated, violations logged with severity); and, where the slice is significant, evidence of a quick **usability walkthrough** (keyboard pass + screen-reader pass + 5-second/first-click sanity). The scorecard separates these **measured** signals from **asserted** judgment.
- **Auditor checks —** `[D]` Lighthouse Perf ≥ 90 and A11y ≥ 90 on changed routes (Lighthouse CI gate, mobile profile) `[D]` Core Web Vitals thresholds met — LCP < 2.5s, INP < 200ms, CLS < 0.1 (Lighthouse/PSI lab; web-vitals field at p75) `[D]` axe-core scan = 0 violations (CI — see *Accessibility* dimension) `[J]` Nielsen 10-heuristic critique completed for the changed flow with severities recorded `[J]` keyboard + screen-reader walkthrough notes attached for non-trivial UI.
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Measuring UX turns "looks fine to me" into evidence a non-designer can trust and a CI gate can enforce, catching regressions automatically; the cost is wiring up Lighthouse/axe in CI and the discipline of a heuristic pass — cheap relative to shipping a slow or inaccessible surface to everyone.
- **Sources —** Google Lighthouse / *Core Web Vitals* thresholds, LCP/INP/CLS (web.dev/articles/vitals, p75 field); INP replaced FID March 2024 (web.dev/articles/inp); Deque axe-core (deque.com/axe); Nielsen-Norman, *10 Usability Heuristics for User Interface Design* (nngroup.com/articles/ten-usability-heuristics/) & *Severity Ratings for Usability Problems* (nngroup.com/articles/how-to-rate-the-severity-of-usability-problems/).

---

## Authoring rules (the catalog meta-rules — do not delete)

- **Additive floor:** add dimensions as you discover them; **never delete** one. This catalog is meant to become "every standard we can think of."
- **Right-size:** apply only *relevant* dimensions per change (`KISS`/`YAGNI`); never skip a relevant one. Relevance is a per-change judgment — see `README.md`.
- **Novel patterns allowed** when they add clear value — justify (problem → why existing patterns fall short → benefit) and record in `claugentic-DECISIONS.md`. Unconventional ≠ wrong.
- **Every dimension carries a Confidence tag** so the harness can separate what it *proved* (deterministic gates) from what it *asserts* (judgment). Trust the oracle, not the model's word.
