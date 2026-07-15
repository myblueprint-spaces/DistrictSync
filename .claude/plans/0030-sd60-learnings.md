# 0030 — SD60 clarification learnings: email standardization + home-school rostering + drop Active No Primary

- **Status:** Implemented + verified + reviewed (3-lens plan review · correctness re-review PASS · honesty pass) — PR [#51](https://github.com/myblueprint-spaces/DistrictSync/pull/51), awaiting merge + release
- **Resumable from:** §Decomposition Slice 1
- **Blockers:** none
- **Flags:** ⚠️ Deliberately overrides an evidence-based recommendation — see §Decision record.
- **Roadmap item:** n/a (SD60 onboarding follow-up; resolves the "pending SD clarification" from plan 0028)
- **References:** `.claude/plans/0028-sd60-mapping.md` · `docs/claugentic-DECISIONS.md` (2026-07-07 SD60 entry) · `docs/claugentic-ARCHITECTURE_TREE.md` · `src/config/models.py` · `src/etl/transformers/{students,base}.py` · sibling `config/mappings/sd54myedbc_mapping.yaml` (name-based email precedent)

## Problem
SD60 answered the three pending questions from plan 0028. Applying the answers:
1. **Student emails** — SD60 confirmed the pattern is `firstlastNN@learn60.ca` (NN = 2-digit year the student joined the district). User decision (informed, against recommendation): **generate this pattern for every student** so all land on a deterministic `@learn60.ca` identity (also fixes the 1,350 non-whitelisted domains and 736 blanks in one move).
2. **Active No Primary** — SD60: "can be dropped" (online-course-only attachments, not real active enrolments). Currently `sd60myedbc` *retains* them. **Drop them.**
3. **Dual-school students** — SD60: map to **Home School Number**. Currently we keep the home-school *row* and output its `School Number`; SD60 wants the home school as the rostered school directly.

## Evidence (real SD60 extract `data/input/Student_demo_enh.txt`, gitignored; aggregates only — no PII surfaced)
13,353 rows · 8,705 active (Active/PreReg + 37 Active No Primary) · matches SD60's stated figures.
- **Email pattern is real & the name half is reproducible:** 99.9% of the 10,356 learn60 addresses are `firstlast` + exactly 2 digits; `firstlast == legalfirst+legallast` at **93.62%** using strip-non-alphanumeric normalization (91.09% spaces-only; accent-folding adds nothing — 1 non-ASCII name / 8,668).
- **The NN suffix is NOT reliably derivable:** `NN == admission-date year` only **43%**; full `firstlast+admissionYY` reconstructs only **41%** of existing addresses. "Joined the district" ≠ admission date (admission = joined *this* school; transfer/cross-enrolled students joined SD60 earlier). Admission-date year is the **only** join-year proxy in the extract (no "district entry year" column exists).
- **Generation is safe from duplicate-identity conflicts:** over the ~8,280 active students (after dropping ANP + cross-enrollment collapse), `legalfirst+legallast+admissionYY` yields **0 collisions, 0 empty** local parts. The NN suffix is *necessary* for uniqueness (without it, 28 students collide in 14 groups). 10 active rows lack a parseable admission year → `firstlast` with no suffix (still unique).
- **Home School Number is 100% populated** (0 blanks, all/active); differs from per-row `School number` on 1,617 rows → direct mapping is strictly more correct than today's "keep the home-school row" (which picks a wrong school for any student with no row where School == Home).

## Decision record (must be logged honestly in DECISIONS.md)
- **[J] User chose full email generation over the recommended "keep file emails as-is".** Documented trade-off: reproduces ~41% of current learn60 logins exactly; the remaining ~59% are **migrated** to a new deterministic address. This is a **login-identity change** for existing SpacesEDU accounts keyed on the old email — acceptable to the user as a one-time standardization, NOT a silent side effect. Recorded so the choice and its consequence are traceable.
- NN source = admission-date year (best available; 43% match to existing suffix; the only join-year proxy). Named as a known-imperfect proxy, not "the join year".
- Blanks/non-standard-domain cleanup at source (SD60's offered re-cut excluding the Key Online Learning school) remains the recommended complementary fix and is **out of scope** here (SD60-side).

## Goals / Non-goals
- **Goal (capability):** a small, **opt-in, config-driven** extension to email generation so a district can (a) derive a date part (e.g. 2-digit year) from a date column into the template, and (b) sanitize substituted values to `[a-z0-9]`. Additive — every other district's email output is **bit-identical**.
- **Goal (SD60 config):** email `format: "{legal first name}{legal surname}{admission yy}@learn60.ca"` + `sanitize: true` + `derived_dates: {admission yy: {column: "Admission date", date_format: "yy"}}`.
- **Goal (SD60 config):** drop `Active No Primary` → inherit base default `active_values: ["Active","PreReg"]`.
- **Goal (SD60 config):** `SchoolCode: "Home school number"` (roster every student under home school; keep `cross_enrollment.collapse` for row-dedup).
- **Non-goal:** matching the exact existing learn60 addresses (impossible — NN not derivable); source-side blank/domain cleanup (SD60-side); any change to SD40/48/51/54/74 behavior; new date tokens beyond the existing friendly set (`yyyy/yy/MMMM/MMM/MM/dd`).

## Approach
**Reuse, don't reinvent.** The date-part derivation reuses base's existing, tested `friendly_date_format_to_strftime()` + `format_date()` (same machinery attendance uses). No new date parsing.

1. **`src/config/models.py`** — extend `FieldEmailFormat` (currently `format: str` only):
   - add `sanitize: bool = False`
   - add `derived_dates: dict[str, EmailDerivedDate] = {}` where `EmailDerivedDate(BaseModel, extra="forbid")` has `column: str = Field(min_length=1)` + `date_format: str = Field(min_length=1)` (empty value fails at config load — the intended fail-loud; a bare `str` would silently accept `""`).
   - add `model_config = ConfigDict(extra="forbid")` to `FieldEmailFormat` (fail-loud on typo'd keys; no existing config has extra keys → safe).
   - update `get_raw_field_map()`'s `FieldEmailFormat` branch to round-trip as **plain nested dicts** and **CONDITIONALLY OMIT** `sanitize`/`derived_dates` when at default (mirrors the `FieldEnrollStatus`/`FieldTransform`/`FieldAcademicYear` branches that omit None/empty): `d = {"format": val.format}; if val.sanitize: d["sanitize"] = True; if val.derived_dates: d["derived_dates"] = {k: v.model_dump() for k, v in val.derived_dates.items()}`. **Why conditional:** keeps `test_sd51_custom_email` (asserts exact `{"format": ...}` equality, `test_config.py:419`) green AND keeps SD40/48/51/54/74 transform output byte-identical (their `_generate_emails` sees no new keys). Emit **`v.model_dump()`** (plain dict), never the model instance — `_generate_emails` reads dict-style.
   - `classify_field` already routes any dict with `"format"` → `FieldEmailFormat`; unchanged.
   - date_format token validity is enforced at transform time by `friendly_date_format_to_strftime` (fail-loud `ValueError`), consistent with attendance; keep config-layer free of a transformer import (no layer breach).
2. **`src/etl/transformers/base.py`** — `generate_student_email(row, format_str, sanitize=False)`: when `sanitize`, reduce each substituted string value to `[a-z0-9]` (lowercase) instead of the default lowercase+strip-spaces. Default path unchanged (SD54/SD40/… identical). Also add a tiny helper `derive_date_part(value, strftime_fmt) -> str` that reuses `_coerce_date` and returns `parsed.strftime(fmt) if parsed else ""` — i.e. **empty on blank OR unparseable** (NOT `format_date`'s passthrough-original, which would leak a garbage suffix like `firstlastunknown` under `sanitize`).
3. **`src/etl/transformers/base.py` facade** `src/etl/transformer.py:175` — `DataTransformer.generate_student_email` gains `sanitize=False` and forwards it (legacy passthrough kept in single-source sync; 2-arg callers unaffected).
4. **`src/etl/transformers/students.py`** — `_generate_emails`: read `sanitize` + `derived_dates`; when `derived_dates` present, work on a `working.copy()`, inject each pseudo-field as a column computed by `self.derive_date_part(col_value, friendly_date_format_to_strftime(date_format))` (empty-on-unparseable — matches the offline uniqueness analysis exactly); **fail-loud `ValueError`** if a derived `column` is absent (validate at boundary, mirrors `_collapse_cross_enrollment`/`apply_row_filters`); pass `sanitize` through to `generate_student_email`. Pseudo-field keys are lower-cased to match the lower-cased format string (spaces-in-keys already supported — SD54 uses them).
5. **`config/mappings/sd60myedbc_mapping.yaml`** — Students block: replace the `EnrollStatus` override (drop ANP → inherit base default), add `SchoolCode: "Home school number"`, add the `Email Address` block above. Update header comment (emails now generated; ANP dropped; home-school rostering). Keep `cross_enrollment.collapse: true` **with a comment**: because `SchoolCode == home_school_column`, the collapse's home-row priority is uniformly 0 → it degenerates to *keep-first-row-per-User-ID*. This is **outcome-deterministic and safe** — empirically (real extract, 2026-07-14) all 386 cross-enrolled students have **identical admission-year AND identical homeroom across their rows** (0 vary), and first-row vs home-row selection yields **0 differing emails**; every field the surviving row contributes is invariant to the choice. (If a future extract ever shows cross-row variance, revisit with an explicit `cross_enrollment.school_column`; not built now — YAGNI.)

## Architecture & holistic fit
- **Layering preserved** — schema in `models.py` (single source, round-tripped via `to_raw_dict`/`get_raw_field_map`); derivation in the transformer layer reusing base date helpers; config selects behavior. No config→transformer import.
- **SOLID/DRY/KISS/YAGNI** — one opt-in capability that generalizes (any date part, any district), reusing existing date machinery; no speculative tokens/columns. `sanitize` + `derived_dates` both default off → zero blast radius.
- **Configurable Columns rule** — every source column (name fields, admission date, home school) comes from `field_map`/config, none hardcoded in transformer code.
- **Quality dimensions** (`docs/claugentic-standards/`): `data-and-persistence` (deterministic, unique, complete addresses), `privacy` (analysis used aggregates only; no PII logged — email gen logs nothing per-row; fail-loud logs column names only), `reliability-resilience` (fail-loud on missing derived column / bad token), `maintainability-structure` (opt-in schema, reuse), `testing` (model round-trip, transformer derivation+sanitize, SD60 config assertions, other-districts-unchanged), `observability-ops` (no new PII surface), `product-ux` (every SD60 student gets a working-shaped `@learn60.ca` login; consequence recorded).

## Affected files
- `src/config/models.py` — `EmailDerivedDate` model (`column`/`date_format` both `min_length=1`); extend `FieldEmailFormat` (`sanitize`, `derived_dates`, `extra="forbid"`); **conditional-omit plain-dict** round-trip in `get_raw_field_map`.
- `src/etl/transformers/base.py` — `generate_student_email` gains `sanitize`; new `derive_date_part` (empty-on-unparseable) helper.
- `src/etl/transformer.py` — facade `generate_student_email` forwards `sanitize`.
- `src/etl/transformers/students.py` — `_generate_emails` gains derived-date injection + sanitize + fail-loud missing-column.
- `config/mappings/sd60myedbc_mapping.yaml` — email block, drop ANP, `SchoolCode → Home school number`, header + collapse-degeneracy comments.
- `tests/test_config.py` — flip `test_active_values_include_active_no_primary` → assert ANP NOT retained (inherits default); add SD60 `SchoolCode == "Home school number"`; add SD60 email `format`/`sanitize`/`derived_dates` round-trip assertions; add `EmailDerivedDate`/`FieldEmailFormat` model tests (valid + `extra="forbid"` fail + `min_length` empty-value fail). **Confirm `test_sd51_custom_email` (`:419`) still passes** (proves conditional-omit round-trip) — no edit expected; it is the regression tripwire.
- `tests/test_transform_students.py` — email generation with `derived_dates` (2-digit year from a `DD-MMM-YYYY` date), `sanitize` strips apostrophe/hyphen/space, **unparseable/blank admission date → no-suffix (not garbage)**, missing derived column raises, no-`derived_dates`/no-`sanitize` path unchanged (SD54-shape regression guard).
- `docs/claugentic-DECISIONS.md` — dated entry (§Decision record above; the [J] override + consequence + NN-proxy honesty).
- `docs/claugentic-ARCHITECTURE_TREE.md` — update `sd60myedbc` line + `models.py`/`students.py`/`base.py` notes (git-hook enforced).
- `CLAUDE.md` — note the opt-in email `derived_dates`/`sanitize` capability under email-format templates + Configurable Columns; update the SD60 one-liner (emails now generated, ANP dropped, home-school rostered).

## Verification (Definition of Done)
- New tests green; **full suite** green (proves SD40/48/51/54/74 email output unchanged — the regression guard).
- `make validate-config` (all 11 configs) green; SD74 snapshot unchanged; ruff + mypy(non-UI) + bandit green; tree-check green. **Note:** `validate-config` green does NOT prove the SD60 `date_format` token is valid (token validity is a transform-time check); the SD60 `--dry-run` below is the real gate for the token.
- Manual data check (privacy-preserving): run SD60 conversion (`--dry-run` or to a scratch output) and confirm Students.csv row count ≈ 8,280, 0 blank emails, 0 duplicate emails, every email `@learn60.ca`, SchoolCode == home school. Report **aggregates only**.
- DECISIONS.md records the override + consequence honestly (honesty-reviewer lens on the wording).

## Decomposition (single slice — one specialist can land complete)
**Slice 1:** models + base + students + sd60 config + all tests + docs, verified end-to-end. Small, cohesive, no half-state. Adversarial verify (architect-review + yagni + honesty on the decision copy) before land.

## Risks & mitigations
- **Regressing other districts' emails** → `sanitize`/`derived_dates` default off; full suite + SD74 snapshot are the guard; explicit SD54-shape unit test.
- **`extra="forbid"` on `FieldEmailFormat` breaks a config** → no existing email config carries extra keys (verified: only bare `format:`); add a round-trip test.
- **Login churn (the ~59%)** → not a code bug but a product consequence; recorded in DECISIONS + config comment; complementary source-side fix noted as SD60-side follow-up.
- **Missing/garbled admission date** → the `derive_date_part` helper yields "" for blank OR unparseable values (via `_coerce_date`), so a bad value → `firstlast` with no suffix (never a garbage `firstlastunknown` suffix); still unique per the data (0 blanks after generation). Fail-loud only for a *missing column*, not a bad *value* (row-resilient), matching the data-errors philosophy. Real extract has 0 unparseable-non-empty + 10 blank admission dates among active rows.

## Plan review (0030-R1) — 3-lens adversarial pass, all findings resolved
- **yagni: APPROVE_WITH_NITS** — capability proportionate (both knobs trace to measured need; DRY reuse of date machinery; accent-folding + config-load-token-validation correctly cut). Nits (keep `derived_dates` as a map — idiomatic; global `sanitize` — accept) taken as-is.
- **design: APPROVE_WITH_NITS** — layering/round-trip/`extra="forbid"` all sound. Resolved: (a) round-trip emits plain dicts via `model_dump`, conditional-omit; (b) `EmailDerivedDate` fields `min_length=1`; (c) facade wrapper forwards `sanitize`; (d) config comment on collapse degeneracy; (e) Verification notes the token isn't proven by validate-config.
- **adversarial: CHANGES_REQUIRED → resolved.** MED#1 round-trip would fail `test_sd51_custom_email` → fixed by conditional-omit (spec'd above). MED#2 collapse determinism → **data shows non-issue** (386 cross-enrolled: admission-year & homeroom vary 0; first-vs-home selection → 0 differing emails); documented, no `school_column` added (YAGNI). MED#3 `format_date` garbage passthrough → fixed by `derive_date_part` empty-on-unparseable (and data has 0 such rows); the offline uniqueness analysis used the *same* coercion → pipeline == analysis (8,280 students, 0 collisions, 0 blanks).
