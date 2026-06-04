# 0001 — Config field-mapping: validate-then-discard → field-level Strategy

- **Status:** Reviewed → Spec'd (Slice 1a) — awaiting user approval (Stage 5)
- **Roadmap item:** `docs/ROADMAP.md` → T1.1
- **References:** `docs/ARCHITECTURE_TREE.md` · `docs/DECISIONS.md` (config decisions) · architect review 2026-06-04

## Problem
The config type-layer is **write-only**. Flow: `YAML → classify_field → typed Pydantic models → to_raw_dict() → dict[str,Any] → transformers`.
- `models.py:92-125` `classify_field` detects 8 field kinds by probing dict keys.
- `pipeline.py:141` calls `config.to_raw_dict()` and immediately downgrades the validated models back to raw dicts.
- `base.py:362-416` `apply_field_map` **re-implements** the same taxonomy as an `isinstance`/key ladder — a second classifier that can drift from the first.
- The typed `FieldMapping` union + `get_entity()` have **zero production consumers** (only tests).
- The dict-shape sniffing is duplicated **4+ times**: `models.classify_field`, `base.apply_field_map`, `classes.py` (`_assign_grades`/`_assign_class_names`), `enrollments.py`/`context.py` column resolution.
- Adding a 9th field type touches **7+ edit sites**, two order-sensitive (OCP failure). `mypy` guards nothing past `pipeline.py:141`.

## Goals / Non-goals
- **Goal:** make the validated typed config the single source of truth; dispatch field handling polymorphically; delete the `to_raw_dict()` round-trip for `field_map`.
- **Goal:** adding a field type = one new class, registered once.
- **Non-goal:** changing YAML config syntax (no new `kind:` key in YAML — keep all existing configs working).
- **Non-goal:** the `student_courses.py` hardcoded-columns migration (separate ROADMAP item) or `enabled_entities` refactor (T2.2).
- **Non-goal:** any change to output bytes — the SD74 golden snapshot must stay identical.

## Approach
Give each handler model an `apply(working: DataFrame, target: str, ctx: TransformContext) -> Series` method (field-level **Strategy**). `target` is required — academic-year picks start vs end from the *target field name* (`base.py:379`). Add a `FieldDirect` model for the bare-`str`/`None` case so every kind is one class and "add a type = one class" actually holds (today there are only 7 `Field*` classes; direct is unmodeled). `apply_field_map` collapses to:
```python
for target, handler in field_map.items():
    result[target] = handler.apply(working, target, ctx)
```
`classify_field` stays the **single** classifier — it already runs in `EntityConfig.validate_fields` (`mode="after"`); reuse it, do *not* add a `mode="before"` discriminator. YAML is unchanged.

**Boundary mechanism (explicit, per review):** transformers today get a **raw** `mapping` dict (`pipeline.py:141` `to_raw_dict()` → `DataTransformer.transform(mapping: dict, ...)`). Slice 1a routes `apply_field_map` through `.apply()` while *keeping* that raw-dict boundary (convert each entity's `field_map` to typed handlers at the top of `apply_field_map`). Slice 1b removes the downgrade by having `transform()` accept the typed `EntityConfig.field_map`. This is what lets each slice land complete.

`ALLOWED_TRANSFORMS` (Slice 3) moves to a shared home importable by both `config` and `etl` (e.g. `src/etl/transforms.py`) to avoid a `config → etl` import cycle.

Alternatives rejected: (a) explicit `kind:` in YAML — breaks every existing config; (b) delete the unused typed layer — loses the OCP win.

## Affected files
- `src/config/models.py` — `Field*` models gain `.apply()`; `classify_field` becomes the discriminator (one source of truth); remove `get_raw_field_map`; `field_map` typed `dict[str, FieldMapping]`; `FieldTransform` validates against `ALLOWED_TRANSFORMS` at load.
- `src/etl/transformers/base.py` — `apply_field_map` → dispatch loop; remove the `isinstance` ladder + the runtime transform-allowlist check + broad `except` swallow.
- `src/etl/pipeline.py` — pass typed `EntityConfig` (or `field_map`) to transformers; stop the `to_raw_dict()` field_map round-trip.
- `src/etl/transformers/students.py` — fold the email-format special-case (`_generate_emails`) into `FieldEmailFormat.apply` (or have it consume the typed handler), so the taxonomy is complete.
- `classes.py`, `enrollments.py`, `context.py` — migrate the secondary dict-sniffing sites to typed access (Slice 2).
- Tests: per-`Field*` `.apply()` unit tests; keep `tests/test_config.py` green; SD74 snapshot must not change.

## Risks & mitigations
- **Output drift** (esp. email generation, academic-year dates, append-year IDs) → SD74 snapshot regression + the 640-test suite must stay green; add explicit per-handler tests *before* deleting the old path.
- **Load-bearing classify ordering** (e.g. `format` before `transform`; bare `column` falls through to transform) → preserve exact precedence in the discriminator; cover with tests for each shape.
- **`to_raw_dict()` has non-field_map users** (source_files, global_config, `02_Convert.py:169`, `04_Mapping_Editor.py:65`) → only remove the *field_map* portion in Slice 1; leave the rest until Slice 2/T1.3.

## Test strategy
1. New `tests/test_field_handlers.py` — one test per `Field*.apply()` (direct, transform, fixed, academic-year, append-year, email-format, name-config, id-role).
2. Existing `tests/test_config.py` + all transformer tests stay green.
3. SD74 `tests/test_regression_sd74.py` byte-identical (the safety net for output drift).
4. A negative test: a YAML transform not in `ALLOWED_TRANSFORMS` now fails **at config load** (loud), not at row time.

## Decomposition (slices)  _(re-sliced per Stage-3 review)_
Each slice lands complete (code + tests + docs), green on all gates, no debt.
- [ ] **Slice 1a — typed dispatch inside `apply_field_map` (behind the existing dict boundary).** Add `FieldDirect` + `.apply(working, target, ctx)` to every handler; convert the raw `field_map` to typed handlers at the top of `apply_field_map` via `classify_field`; route through `.apply()`; fold the email-format case in from `students._generate_emails`; delete the `isinstance` ladder. *Lands complete:* one classifier exercised, dispatch live, all tests + SD74 snapshot green; raw-dict `transform()` contract unchanged.
- [ ] **Slice 1b — remove the field_map downgrade.** `transform()`/`pipeline.py` pass the typed `EntityConfig.field_map` (`dict[str, FieldMapping]`); drop the field_map path of `to_raw_dict()`/`get_raw_field_map()`. *Lands complete:* no field-spec dict-sniffing on the main path; mypy sees real types. (`to_raw_dict` may remain for `source_files`/`global_config` — legitimate, no debt.)
- [ ] **Slice 2 — migrate the remaining sniffing sites.** `classes._assign_grades`/`_assign_class_names`, `enrollments`/`context` column resolution, the shared base helpers `resolve_date`/`assign_class_ids` (`base.py:232-277`), and the duplicated `02_Convert.py:163-209` path (or fold into T1.3's shared core). *Lands complete:* no dict-shape probing of field specs anywhere.
- [ ] **Slice 3 — enforce `ALLOWED_TRANSFORMS` at load.** Move the allowlist to a shared home (see Approach); validate `FieldTransform.transform` against it in the model; remove the runtime check + swallowed `ValueError` (`base.py:395`,`412`); add the negative load-time test. *Lands complete:* security at the boundary, fail-loud.

---

## Review  _(filled by plan-reviewer, Stage 3)_

**Verdict: CHANGES REQUIRED**

The diagnosis is correct and the Strategy direction is the right OCP/DIP fix — but the plan materially **understates the blast radius** and several concrete claims don't match the code. The slicing assumes transformers already consume typed objects; they don't. As written, Slice 1 cannot land vertically complete. Fix the items below, then it's a strong plan.

### Required changes (actionable)

1. **Transformers consume raw dicts, not typed objects — say so and re-scope.** The plan implies "transformers consume the typed `EntityConfig.field_map`" is a small step. In reality `pipeline.py:141` calls `to_raw_dict()` → `mappings: dict[str, dict]` (`pipeline.py:142`), and `DataTransformer.transform()` (`transformer.py:93-104`) receives a **raw `mapping` dict** and a **raw `global_config` dict**, storing the latter on `context.global_config` (`context.py:18-23`). Every transformer does `field_map = mapping.get("field_map", {})` (`students.py:19`, `staff.py:19`, `classes.py:31`, `enrollments.py:19`, `blended.py:38`, `registry.py:34`). Switching `apply_field_map` to typed `.apply()` requires re-typing the whole `transform()` contract OR converting back to typed objects at the boundary. State the chosen mechanism explicitly: either (a) `DataTransformer.transform()` takes the typed `EntityConfig`, or (b) `pipeline.py` builds `dict[str, FieldMapping]` and passes it alongside. Don't leave it as "pipeline passes the typed field_map."

2. **Add a `FieldDirect` model (or explicitly justify not having one).** There are only **7 `Field*` classes** (models.py:21-75); the "8th type" is the bare `str`/`None` direct case with no class. The plan's headline win — "adding a field type = one new class, dispatch loop has no special cases" — is unreachable while direct mapping lives as `str`/`None` in the `FieldMapping` union (models.py:79-89) and gets special-cased in the loop (base.py:405-410). Either introduce `FieldDirect(column: str)` so *every* branch is a class with `.apply()` (preferred — delivers the real OCP win), or keep `str`/`None` and admit the dispatch loop retains a non-class fallback. Note ARCHITECTURE_TREE.md:41 and CLAUDE.md:115 both say "8 field mapping types (...`FieldDirect`...)" — a `FieldDirect` that doesn't exist — so the docs already assume this; reconcile it here and in Stage 9.

3. **Fix the `.apply()` signature — it must take the target field name.** The proposed `apply(working, ctx) -> Series` cannot implement three handlers: `FieldAcademicYear` chooses `ctx.academic_start` vs `ctx.academic_end` purely from whether the **target** is "Start Date" or "End Date" (base.py:379) — it never reads `working`; `FieldEmailFormat`/`FieldNameConfig`/`FieldAppendYear` are row-wise (`working.apply(...)`, base.py:382-387, students.py:86) and some are multi-column. Change the contract to `apply(working: DataFrame, target: str, ctx: TransformContext) -> Series` (or have `apply_field_map` pass `target`). Without this the academic-year handler is unimplementable and the snapshot will drift.

4. **`model_validator(mode="before")` framing is wrong — drop it.** `EntityConfig.validate_fields` already runs `classify_field` in `mode="after"` and stores typed objects on `self.field_map` (models.py:155-162). There is no need for a `mode="before"` discriminator; `classify_field` is *already* the single dict-shape→type mapper (models.py:92-125). The actual problem is that `to_raw_dict`/`get_raw_field_map` (models.py:253-290) then **downgrades them back to dicts** and the loop in `base.py:362-416` is a second classifier. Reframe the approach as: "keep `classify_field` as the sole discriminator (it already runs at load); stop the `get_raw_field_map` downgrade; dispatch on the already-typed objects." This is accurate and simpler than what's written.

5. **List `src/ui/pages/02_Convert.py` as an affected/at-risk consumer.** It is a **second, fully duplicated pipeline** (`02_Convert.py:163-209`) that also calls `to_raw_dict()` (line 169) and feeds raw dicts to `DataTransformer.transform()`. The plan's "non-field_map users of `to_raw_dict`" risk note misses that Convert also drives the *transformer* path, so re-typing `transform()` (change #1) breaks it. Either keep `transform()` back-compatible with dicts in Slice 1, or pull the T1.3 pipeline-core extraction forward. Decide and write it down. (`04_Mapping_Editor.py:65` only reads source_files/global_config, so it's lower-risk — confirm.)

6. **Make `assign_class_ids` / `resolve_date` part of the scope, not an afterthought.** Slice 2's "migrate secondary sniffing" is bigger than "classes/enrollments/context." The dict-shape probing also lives in the **shared base helpers** `resolve_date` (base.py:232-240) and `assign_class_ids` (base.py:251-277, reads `field_map.get("Class ID")` as dict), called from `classes.py:132/192/206/250` and `enrollments.py:161/185`. These cannot be migrated to typed access without touching the Classes→Enrollments shared path — which overlaps T1.2 (debloat base) and T2.1 (typed handoff). Either pull these helpers explicitly into Slice 2 with their callers, or carve a Slice 2a for them. As written, Slice 2 silently inherits a large surface.

7. **`get_entity()`/`FieldMapping` "zero production consumers" is correct — keep the win, but verify the negative-test claim.** Confirmed: `get_entity` (models.py:220) and the typed union are only used in `tests/test_config.py`. Good. But the Slice 3 negative test ("transform not in `ALLOWED_TRANSFORMS` fails at load") must add a model-level validator on `FieldTransform.transform` (models.py:21-25) — note that `classify_field`/`FieldTransform` currently accept *any* transform string, and `test_config.py` constructs `FieldTransform(column=..., transform="grade_to_ceds")` directly, so the allowlist must be importable into `models.py` without a circular import (`ALLOWED_TRANSFORMS` lives on `BaseTransformer`, base.py:27-33). Call out where the allowlist constant moves to avoid a `config → etl` import cycle — likely a shared constants module. This is a real design decision the plan omits.

### Sizing / completeness check

- **Slice 1 — split needed.** With #1 + #3 + #5 folded in, Slice 1 = re-type the `transform()` contract (or boundary-convert) + add `.apply()` to 7 (or 8 w/ `FieldDirect`) handlers + fold the email special-case + keep Convert working + keep SD74 byte-identical. That's the whole risky core in one session. **Split:**
  - **1a — typed `.apply()` behind the existing dict boundary.** Add `.apply(working, target, ctx)` to every handler; rewrite `apply_field_map` to *classify-then-dispatch* internally (call `classify_field` on each raw value, then `handler.apply(...)`), keeping `transform()`'s dict signature unchanged. Fold in the email case. Net: one classifier of record, dispatch loop live, **zero** changes to pipeline/Convert. Snapshot + full suite green. Lands complete.
  - **1b — stop the downgrade at the boundary.** Change `DataTransformer.transform()` (and `pipeline.py` + `02_Convert.py`) to pass typed `field_map`; delete the per-call `classify_field` added in 1a. Lands complete, mypy now sees types through the loop.
  This keeps each session's risk bounded and each landing green.
- **Slice 2 — split or expand.** As written it omits `resolve_date`/`assign_class_ids` (#6) and entangles T1.2/T2.1. Either explicitly include those base helpers + their `classes.py`/`enrollments.py` callers, or carve **2a (field-map sniffing in classes/enrollments/context)** from **2b (shared `resolve_date`/`assign_class_ids` base helpers)**. Removing `get_raw_field_map` + the `to_raw_dict` field_map branch only lands clean once **all** dict-readers are gone — sequence it last.
- **Slice 3 — OK,** contingent on #7 (resolve the allowlist import-cycle / constants home). Self-contained and lands complete once that's specified.

### Harness impact

- **Doc fix (Stage 9):** ARCHITECTURE_TREE.md:41 and CLAUDE.md:115 reference a non-existent `FieldDirect` and assert "8 field mapping types." Either add `FieldDirect` (change #2) to make the docs true, or correct both to "7 typed + direct `str`/`None`." Update `docs/developer/architecture.md:170-172` (the `classify_field`/`to_raw_dict` description) when the round-trip is removed.
- **Candidate STANDARD:** "Validated config is the single source of truth — no `to_raw_dict()`-style downgrade of typed models back to dicts before use." This recurs (T1.3, T2.4 "stop re-defending validated config"). Promote once Slice 1b lands as the reference pattern.
- **DECISIONS entry** on landing: the `ALLOWED_TRANSFORMS` home (shared constants module vs staying on `BaseTransformer`) and the `apply(working, target, ctx)` contract — both are cross-cutting choices T2.4 will lean on.
- **No new agent** needed; `implementer-architect` covers it.

---

## Spec  _(Slice 1a — Stage 4; awaiting user approval)_

### Slice 1a — typed dispatch inside `apply_field_map`
**Files & changes**
- `src/config/models.py`:
  - Add `class FieldDirect(BaseModel)` (`column: str`) for the bare-`str`/`None` case; add to the `FieldMapping` union; `classify_field` returns it for a bare string, preserving current precedence (format → name-config → id-role → academic-year → append-year → fixed-value → transform → **direct**).
  - Add `apply(self, working, target, ctx) -> pd.Series` to each handler (`FieldDirect`, `FieldTransform`, `FieldFixedValue`, `FieldAcademicYear`, `FieldAppendYear`, `FieldEmailFormat`, `FieldNameConfig`, `FieldIdRolePair`), each encapsulating the logic now in the `apply_field_map` ladder + `students._generate_emails` + `resolve_date`. Transform callables resolved via `ctx` (no `config→etl` import).
- `src/etl/transformers/base.py`:
  - `apply_field_map`: build `handlers = {t: classify_field(t, spec) for t, spec in field_map.items()}`, then `for t, h in handlers.items(): result[t] = h.apply(working, t, ctx)`. Remove the `isinstance` ladder. Leave the runtime allowlist check (Slice 3 removes it).
**Tests to add** (`tests/test_field_handlers.py`)
- One per handler `.apply()`: direct (bare string), transform, fixed value, academic-year (start *and* end by `target`), append-year, email-format, name-config, id-role.
**Acceptance criteria**
- `pytest tests/ -q` incl. `test_regression_sd74.py` **byte-identical**; `python scripts/check_architecture_tree.py`, ruff check+format, `mypy src/ --exclude 'src/ui'`, bandit — all green.
- No public-behavior change; `apply_field_map` contains no `isinstance`/key ladder.

### Slices 1b–3
Spec'd after Slice 1a lands (each at its own approval gate).
