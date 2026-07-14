<!-- claugentic-dev-harness@0.1.40 managed — do not edit (copied from the claugentic-dev-harness plugin) -->
# PRODUCT_SPEC — what this product is supposed to be

> **What this file is.** A plain-English, durable statement of what your product *promises* —
> who it's for, the job it does, each feature's flow and the states that matter — ending in a
> machine-readable list of **acceptance criteria** a check can later run against the real
> product. This is the **template** (the pristine contract). Your filled copy lives at
> `docs/claugentic-PRODUCT_SPEC.md`.
>
> **How it's made & owned.** The filled `docs/claugentic-PRODUCT_SPEC.md` is **user-owned** — it is
> **never stamped** and **never auto-refreshed** by `init`; it is built and refreshed only by
> **`/claugentic-dev-harness:product` spec mode**, a plain-English conversation (the
> `product-designer` role convened) that walks you through each section. Two checks read it:
> **`/claugentic-dev-harness:product` gap mode** reads your *code* against the criteria
> (static — it does **not** run the app) and writes the gaps into the audit backlog; the
> **QA workflow (`qa.js`)** can later *run the app* and drive the criteria for real.
>
> Keep the prose **plain-English first** — a non-engineer reads it to decide if the product is
> what it should be. Right-size: a small product needs a few features; don't pad it (KISS/YAGNI).

---

## Who it's for

*Describe the real person this product serves — who they are, the context they're in, and what
they're equipped (or not) to do. One short paragraph. (See `docs/claugentic-PRODUCT.md` for the durable
cross-product user/design-language context, if you keep one.)*

## The job-to-be-done

*In the user's own words: "When I ___, I want to ___, so I can ___." The outcome they hire this
product for — not the features, the result.*

## The promise

*One short paragraph: what this product commits to **being** for that user. The single sentence
you'd want them to repeat about it. The Features below are how the promise is kept; the
Acceptance criteria are how you check it's kept.*

## Features

*One subsection per feature. Per feature, three things:*

### <Feature name>

- **Flow** — the ordered, plain-English steps the user takes to get the outcome (the happy path,
  numbered or dashed). Each step is one user action.
- **States** — the non-happy states this feature's async surfaces must handle. The bar for
  **loading / empty / error** is the standard, not restated here — see
  [`docs/claugentic-standards/product-ux.md`](claugentic-standards/product-ux.md) → *Loading / empty / error states*
  (every async surface has all three). Flow-completeness (no dead ends) is the same standard's
  *User-flow completeness* section. **Point at the standard; don't re-describe it here.** Name
  only which states *this* surface actually has.
- **What good feels like** — the experience qualities that matter here (look-and-feel, perceived
  performance, the feeling you're going for), in plain English.

*(Repeat the `### <Feature name>` block per feature. Each feature heading must appear verbatim in
its criteria's `feature` field below.)*

## Acceptance criteria

The checkable projection of the Features above. **One fenced ```json block, an array of criteria
in the FROZEN schema** — field names exact, and they may **never drift** (the same schema is
pinned in `engine/audit.js` for gap mode and consumed by `engine/qa.js` for runtime checks).

**The schema (each criterion):**

- `id` — a unique string. Suggested form `AC-<feature-slug>-<n>` (e.g. `AC-add-item-1`).
- `feature` — the feature heading above, **verbatim**.
- `flow` — an ordered array (≥1) of plain-English user actions, each performable in a browser or
  as an HTTP call.
- `expect` — an array (≥1) of observable outcomes (visible text, element present/absent, a URL, a
  count, an HTTP status). **All** must hold for the criterion to pass.
- `states` — a subset of `["empty","loading","error"]` (may be `[]`): which product-ux state
  checks to run on this surface.
- `check` — one of `"e2e" | "api" | "manual"`.

**`check` semantics (one line each):** `e2e` = driven in a real browser by the QA workflow ·
`api` = checked at the app's interface (an HTTP call), no browser · `manual` = a human check the
QA run **lists** for a person but **never claims** as passed itself. *(The full runtime semantics
of `flow`/`expect`/`states`/`check` — how each is driven and what pass/fail/not-checkable mean —
are owned by `qa.js` and documented in `docs/claugentic-DECISIONS.md`; the **field names are frozen here**
and the gap check pins them by a test.)*

**Two registers to keep honest:** the **Features prose is the narrative source** and these
criteria are its **checkable projection** — when they disagree, **fix the spec** (the prose and
the criteria are meant to say the same thing). And a criterion is what a QA run *attempts*: a
green run **reduces the risk** the product diverged from intent — it is **never proof the product
is good**.

```json
[
  {
    "id": "AC-add-item-1",
    "feature": "Add an item",
    "flow": [
      "Open the home page",
      "Type 'milk' into the New item field",
      "Click the Add button"
    ],
    "expect": [
      "the list shows an item reading 'milk'",
      "the New item field is cleared"
    ],
    "states": ["empty", "error"],
    "check": "e2e"
  }
]
```
