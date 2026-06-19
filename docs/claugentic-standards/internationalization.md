<!-- claugentic-dev-harness@0.1.40 managed — do not edit (copied from the claugentic-dev-harness plugin) -->
---
module: internationalization
title: Internationalization
version: 0.1.0
status: draft
iso_25010: [interaction-capability]
load_scope:
  keywords: [i18n, l10n, locale, timezone, translation, encoding, currency, rtl]
  globs: ["**/locales/**", "**/i18n/**"]
last_reviewed: 2026-06-04
---

# Internationalization — correct behaviour across locales, timezones, and scripts

> **Loads when:** changes touch locale handling, date/number/currency formatting, string translation pipelines, timezone logic, character encoding, or RTL layout.
> **ISO/IEC 25010:** interaction-capability · **Status:** draft · **v0.1.0**
>
> **Accessibility** (WCAG, keyboard nav, contrast, screen-reader) is covered in `product-ux.md` — cross-reference that module; do not duplicate those standards here.

Each entry below is one **auditable dimension**. Per change, the reviewer applies the
*relevant* ones **fully** (select-don't-skip), right-sized to the change — never
gold-plating an irrelevant one, never skipping a relevant one.

---

## Character encoding

- **Good looks like —** All text is stored, transmitted, and processed as UTF-8. No encoding is assumed implicitly; byte-order marks are handled correctly; binary data is never confused with text.
- **Auditor checks —** Confirm source files and DB columns are UTF-8 `[D]`; check that file/stream open calls do not omit an explicit encoding `[J]`; verify no `latin-1`/`ascii` assumptions in string manipulation `[J]`.
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** UTF-8 everywhere eliminates an entire class of mojibake bugs at near-zero cost; the only cost is discipline at every I/O boundary. Skipping it means data corruption for any user whose name contains a non-ASCII character.
- **Sources —** Unicode Consortium "UTF-8, a transformation format of ISO 10646" (RFC 3629); W3C "Character encodings: Essential concepts" (https://www.w3.org/International/articles/definitions-characters/).

---

## Locale-aware date, time, and number formatting

- **Good looks like —** Dates, times, numbers, and currency amounts are formatted using the user's locale, not the server's. Parsing is locale-aware. No `MM/DD/YYYY` vs `DD/MM/YYYY` ambiguity exists in stored or transmitted data (use ISO 8601 internally).
- **Auditor checks —** Confirm display formatting passes a locale/format parameter `[J]`; verify wire formats use ISO 8601 for dates and integers/decimals for numbers `[D]`; check that no locale-dependent `parseInt`/`parseFloat` or `strptime` calls lack explicit locale handling `[J]`.
- **Confidence —** `judgment` — locale-correctness of format calls is a reviewer call.
- **Tradeoff (plain English) —** Locale-aware formatting makes your product feel native to each user; the cost is a slightly more verbose API (always pass locale). Skipping it means European users see "1.234" as one-thousand-two-hundred-thirty-four when you meant one point two three four.
- **Sources —** CLDR (Common Locale Data Repository) Project (https://cldr.unicode.org/); ISO 8601:2004 "Data elements and interchange formats — Date and time".

---

## Timezone handling

- **Good looks like —** All timestamps are stored in UTC. Conversion to local time happens only at the display layer, using the user's explicit timezone preference. Daylight-saving transitions are handled by a proven library, not manual offset arithmetic.
- **Auditor checks —** Confirm DB timestamps are UTC-typed (timestamptz or equivalent) `[D]`; verify no raw `+offset` arithmetic in business logic — use `pytz`/`dateutil`/`Intl.DateTimeFormat`/`Temporal` `[J]`; check that user timezone is read from a preference, not inferred from server locale `[J]`.
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Storing UTC and converting at display time is the only approach that survives DST changes, user travel, and server relocation; the cost is remembering the rule. Mixing timezones in storage produces phantom duplicated or missing hours around every DST boundary.
- **Sources —** Jon Skeet "Storing UTC is not a silver bullet" (https://codeblog.jonskeet.uk/2019/03/27/storing-utc-is-not-a-silver-bullet/) — nuance on when UTC alone is insufficient; IANA Time Zone Database (https://www.iana.org/time-zones).

---

## Translatable strings

- **Good looks like —** Every user-visible string is externalised into a translation file (e.g. `.po`, `.json`, `.arb`). No strings are concatenated with grammar assumptions (use named placeholders, not positional). Plural forms use the locale's plural rules, not a single `s` suffix.
- **Auditor checks —** Grep for hardcoded user-visible string literals outside translation files `[D]`; verify concatenations use named parameters (`{name} joined` not `"Welcome " + name`) `[J]`; check plural handling uses a plural-aware library `[J]`.
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Externalising strings makes translation possible without code changes; the upfront cost is discipline at every string literal. Skipping it means translations require a developer for every UI text tweak, which never gets done.
- **Sources —** GNU gettext manual "Preparing Translatable Strings" (https://www.gnu.org/software/gettext/manual/html_node/Preparing-Strings.html); Unicode CLDR plural rules (https://cldr.unicode.org/index/cldr-spec/plural-rules).

---

## RTL and bidirectional text layout

- **Good looks like —** Layouts reflow correctly in right-to-left scripts (Arabic, Hebrew, Persian). Icons and directional UI elements mirror appropriately. Text alignment uses logical (start/end) rather than physical (left/right) CSS properties.
- **Auditor checks —** If the product targets RTL markets, verify logical CSS properties (`margin-inline-start` not `margin-left`) are used `[J]`; check that icon/image assets mirror correctly in RTL mode `[J]`.
- **Confidence —** `judgment` — RTL correctness requires visual inspection.
- **Tradeoff (plain English) —** Supporting RTL doubles your addressable market in the Middle East and North Africa with minimal extra code if built in from the start; retrofitting it later is expensive. If RTL is not a target market, this dimension is not relevant — per-change judgment applies.
- **Sources —** W3C "Internationalization Best Practices: Specifying Language in XHTML & HTML Content" (https://www.w3.org/TR/i18n-html-tech-lang/); MDN "CSS Logical Properties" (https://developer.mozilla.org/en-US/docs/Web/CSS/CSS_logical_properties_and_values).

---

## Locale-dependent parsing bugs

- **Good looks like —** No parsing call assumes a fixed locale. Sorting, case conversion, and string comparison use locale-aware APIs. Regular expressions that match word boundaries work correctly for non-ASCII scripts.
- **Auditor checks —** Flag `sort()`/`.toLowerCase()` on user-visible text without locale param `[J]`; check regex word-boundary assertions (`\b`) against non-Latin input requirements `[J]`.
- **Confidence —** `judgment` — identifying locale-sensitive call sites requires reading intent.
- **Tradeoff (plain English) —** Locale-aware operations ensure that a Turkish user's "I" sorts and uppercases correctly (the famous dotless-i bug); the cost is slightly more explicit API calls. Skipping causes subtle data corruption that's almost impossible to reproduce in a CI environment.
- **Sources —** Tom Scott "The Problem with Time & Timezones" (YouTube) — illustrates the class of locale assumption bugs; Java `Locale.ROOT` vs `Locale.getDefault()` documentation as a concrete language-level reference.

---

## Authoring rules (the catalog meta-rules — do not delete)

- **Additive floor:** add dimensions as you discover them; **never delete** one. This catalog is meant to become "every standard we can think of."
- **Right-size:** apply only *relevant* dimensions per change (`KISS`/`YAGNI`); never skip a relevant one. Relevance is a per-change judgment — see `README.md`.
- **Novel patterns allowed** when they add clear value — justify (problem → why existing patterns fall short → benefit) and record in `claugentic-DECISIONS.md`. Unconventional ≠ wrong.
- **Every dimension carries a Confidence tag** so the harness can separate what it *proved* (deterministic gates) from what it *asserts* (judgment). Trust the oracle, not the model's word.
