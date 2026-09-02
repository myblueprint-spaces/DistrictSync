# 0044 — District self-service config editor (Phase 2, re-scoped)

- **Status:** S1 LANDED on `claude/plan-0044-implementation-nrdj2z` (2026-09-02; Stage-7 review PASS after one BLOCKING + two SHOULD fixes); S2 next — spec JIT
- **Resumable from:** Stage 4 — write the S2 spec (catalog + identity integration), then the owner approval gate
- **Blockers:** none
- **Flags:**
  - `#3+R2-1 activation scope: ALL THREE sis_type writers gated for user-authored configs (wizard creator flow · Mapping Apply · Settings folders-card Save); Convert has no writer and stays explicit-manual` — reviewable at the spec gate.
  - `#8 domains persistence: chose PERSIST — the matched-tier collapse is the same behavior every shipped district already has; consequence named, D9 auto-seed interplay pinned` — reviewable at the spec gate.
  - `#5 version emission: chose emit-NO-version (inherit the base's) — already supported and pinned` — reviewable at the spec gate.
  - `R2-2 advisory writer: chose extract-the-discipline — the five shared obligations move to a private helper; identity_save and the new creator_save become thin named wrappers, each with its own prefix-derived allowlist; creator_ registered in _ADVISORY_FIELD_PREFIXES with its rationale in the module comment` — reviewable at the spec gate.
  - `R2-4 column sets: chose one ADDITIVE defaulted PipelineResult field (input_columns) over a second parse or an extractor header seam` — reviewable at the spec gate.
  - `S1 sis-emission deviation: the overlay INHERITS sis: MyEducationBC instead of emitting the config id (reviewer finding; DECISIONS 2026-09-02)` — reviewable at the S2 gate.
  - `S1 CI gate: ci.yml runs only on push-to-main / PRs to main, so no CI run exists for the branch yet — the land-gate CI read is OWED at PR time` — owner decides when to open the PR.
  - `S2 marker rule: the "added on this computer" marker keys on FILE PROVENANCE (ConfigSummary.origin == "user"), NOT on the sd<num>custom id — so a YAML support hands a district is marked the same way, which is why the wording must stay true for a file the admin did not author` — reviewable at the S2 gate (wording alternatives in the S2 spec's Open questions).
- **Disposition at close:** per `docs/claugentic-WORKFLOW.md` plan-file lifecycle.
- **Roadmap item:** `docs/claugentic-ROADMAP.md` → "Brief 0037 … Phase 2 = the district self-service config editor" (updated 2026-08-27)
- **References:** `docs/claugentic-ARCHITECTURE_TREE.md` · `docs/claugentic-DECISIONS.md` (2026-08-27 re-scope entry — the authority for everything this plan builds) · `.claude/plans/0037-brief-front-door-district-identity-mapping-creator.md` (superseded scope, retained safety rails) · plan 0038 (Phase 1, landed)

## Problem

A new district can only be onboarded by a vendor-authored YAML, a PR, and a release
(`docs/developer/adding-district.md` is a developer doc, not a district affordance). The
launch page's "my district isn't listed" path (0038 S4a) collects an SD number and then
dead-ends at a support card — `identity_gate.unmapped_sd_number`'s own docstring says the
card "is the only thing that ever reads it … until Phase 2 ships the mapping creator"
(`src/ui_flet/identity_gate.py:310-316`). Meanwhile the enabling machinery already exists
and is idle: the loader searches the user app-data `mappings/` dir FIRST and resolves
`_base:` parents across both search dirs (`src/config/loader.py:74-83`, `:185-222`), so a
thin user-authored overlay is loadable today — nothing writes one.

Owner re-scope (DECISIONS 2026-08-27, superseding D-0037-5): the GDE format is
standardized across MyEd BC districts — header names diverge only under district-side
pre-processing — while **filenames legitimately vary** (district-specific MyEd SFTP
delivery, opaque to the vendor). So a new district needs *filename mapping + policy knobs*,
not column remapping. That is the whole editor.

## Goals / Non-goals

- **Goal:** a district admin can create a working config for an unlisted MyEd BC district
  entirely in-app — district identity (name + SD number + derived email domain),
  files-to-output (`enabled_entities`, 7 entities), one grades form (the three chained
  grade keys), per-entity input filenames — with **no vendor PR and no release**.
- **Goal (restated per reviews #3 + R2-1):** on ALL THREE activation surfaces — the
  wizard's creator flow, Mapping's Apply (`mapping.py:257`), and the Settings
  folders-card Save (`setup.py:1198`, which carries its own live District dropdown AND
  calls `reconcile()` to re-register the nightly with the new `--sis`, making it the most
  consequential of the three) — a **user-authored** config cannot become the active
  district through the app until schema validation passes AND a dry-run against the real
  input folder succeeds and the admin confirms per-entity counts (with a plain-language
  missing-expected-column report — that report LANDS IN S5; S3/S4 gate copy must not
  promise it). These are the exactly-three `sis_type` write sites
  (reviewer-verified). The verified fact is recorded per config (content-hash-keyed) so
  Mapping's Apply and the folders-card Save can check it without re-running; when it is
  absent or stale they route to the gate / refuse with plain copy — shipped configs
  untouched on all three. **Convert is deliberately NOT gated**: it has no `sis_type`
  write site; running any visible config there is an explicit manual act, exactly as for
  shipped configs today. **Honest threat model (R2-3):** the gate prevents an admin
  *mistake* — it is not tamper-proofing; a hand-edited `config.json` or a hand-dropped
  user YAML bypasses every UI gate today and stays in the same trust position as every
  other hand-editable artifact (the ETL's own load-time gates still apply). The
  pre-existing Mapping-Apply-doesn't-re-register hazard is unchanged and already surfaced
  by `post_apply_presentation` (2026-07-15 decision; reviewer-confirmed the notice
  renders); its notice covers user configs the same way.
- **Goal:** user-authored configs are editable later (Mapping screen), exportable for
  support (they are one small readable file), and **visually distinct** from shipped rows
  in every picker. An edit invalidates the verified fact — re-activation re-passes the gate.
- **Goal:** the creator's starting point is a choice among the non-district-scoped shipped
  configs (`myedbc`, `mbp_all`, `mbp_core`, `mbponly`) — the pick becomes `_base:` and
  prefills the entity checkboxes.
- **Non-goal:** field remapping/exclusion, `row_filters`, headerless `headers` blocks,
  `excluded_course_codes` + course-code exclusion patterns, StudentAttendance
  self-service, `cross_enrollment` — ALL deferred to the possible detailed-editor phase
  (ROADMAP entry, 2026-08-27). The forms must not speculatively scaffold for them.
- **Non-goal:** non-BC / non-MyEd-BC SIS support (the open SCOPE entry; the deferred
  detailed editor is its candidate answer).
- **Non-goal:** any YAML text surface (July-15 decision stands — forms only).
- **Non-goal:** the partner docs surface (separate roadmap entry, deliberately decoupled).

## Approach

**Thin overlay.** The editor writes ONE minimal YAML into `user_mappings_dir()`:
`_base: <chosen starting config>` + `district_name` + `district_domains` +
`global_config` (only the grade keys / `enabled_entities` that DIFFER from what the base
resolves) + per-entity `source_files` for only the roles whose filename was changed.
**No `version:` is emitted** (review #5): the overlay inherits the base's declared version
through `_resolve_inheritance` — already supported and pinned by
`TestInheritedVersion::test_version_inherited_from_base_is_gated_in_range` — so the
bundled-only `TestDeclaredRangeVersusSupported` parity stays untouched and the overlay
never claims a minor it doesn't use. Defaults are OMITTED so inheritance stays
byte-identical and vendor base fixes flow through on app update, **with one deliberate
exception** (review #6): whenever the emission includes `class_rostering_grades` or
`student_rostering_grades`, it ALSO emits `homeroom_grades` explicitly — deep merge
REPLACES lists, the chain `homeroom ⊆ class ⊆ student` validates on the RESOLVED config
(`models.py` `check_rostering_grade_scopes`), and the inherited
`["IT","PR","PK","TK","KG","01".."07"]` would otherwise fail a secondary-only district's
load (the `models.py:790-810` deep-merge trap). Runtime untouched: same loader, same
`_base` deep merge, same Pydantic validation, same degraded-catalog behavior for a
hand-corrupted user file.

Rejected alternatives (1 line each):
- *Materialized full copy* — forks the base; future vendor fixes silently don't reach
  self-serve districts (the exact drift hazard behind the July-15 decision).
- *`config.json`-level overrides applied at runtime* — splits mapping truth across two
  files, invisible to CLI/`make validate-config`, not exportable.
- *`row_filters` for grade scoping* — already adjudicated and rejected 2026-08-13
  (wrong grade space, no chain validation, fails open on the dangerous side).
- *Hard-coding `version: '1.11'` on every overlay* — rejected at review (#5): contradicts
  the declare-what-you-use convention (`loader.py:43-71`) and would strand the
  bundled-only declared-range parity test.

**Config id:** `sd<num>custom` (fits `validators._SIS_TYPE_RE` `^[a-zA-Z0-9_]+$`,
`src/utils/validators.py:30`; distinct namespace so a future shipped `sd<num>myedbc`
never collides — and if both exist, both are listed and visually distinguishable).
`identity_gate._SD_CONFIG_RE` (`:62`) already matches the shape (reviewer-verified:
`sd48custom` satisfies `^sd0*48(?!\d)`), so `unmapped_sd_number` retires the not-listed
card with no new matching rule.

**Domain derivation (prefilled, never demanded):** (1) the stored identity email's
domain (`identity_gate` — the admin setting up IS a staff member; the strongest signal),
(2) union with the bundled BC district-number→domains table (owner CSV 2026-08-27,
vendored as a Python data module — see Research), (3) presumptive `sd<num>.bc.ca` only
when both are empty. Shown as a prefilled, CORRECTABLE confirm line — the owner's rule is
"don't treat domains as unknown quantities until entered", not "the table is truth": the
table is **placeholder-quality prefill** (pulled from a public BC doc grouped by district
number, domains taken from school contact emails), so the admin can amend what it seeds,
and each entered/confirmed value passes the same lowercase-domain validation the
`district_domains` field validator enforces.

**Domains ARE persisted into the overlay (adjudication of review #8, flagged).** The
consequence is named: the admin flips from fail-open tier (i) (all 12 rows) to matched
tier (ii) (exactly their config + saved/picked — `mapping_catalog.py:390-471`), which is
**the same scoping every shipped district's matched admin already lives with** (an SD51
admin sees SD51's rows, never `myedbc`), and the honest escape — blank the stored address
in Settings — stands. What a custom district loses (Mapping's "switch to another
pre-built one") is moot for them: they have one config and their affordance is Edit.
What they gain is the full S4a experience on a fresh profile/reinstall: the launch page
matches their address straight to their district. Two obligations ride the choice:
(a) the District-step **auto-seed** (D9) must not fire into or past a pending creator flow
— pinned; (b) CLAUDE.md's "unclaimed = shown in every state" is corrected to "shown in
every UNMATCHED state" (S7 doc fix — the code was always tier-scoped). Neither Home card
needs the domains (reviewer-verified: `unmapped_sd_number` matches by ID;
`matched_excludes_saved` is a non-question with no match).

**UI shape:** the wizard District step gains a "Set up my district" branch (prefilled
from `identity_sd_number` when the launch page stored one). Creator forms at the District
step: starting-point pick → identity confirm → entities → grades (seeded from RESOLVED
values — review #6 — with vocabulary/order derived from `CEDS_MAPPING`,
`grades.py:66-112`; `CEDS_GRADE_CODES` is an unordered frozenset and `"Other"` is never
case-normalised). A creator-mode-only **"Your files" step after Folders** (it needs the
input dir): **role/file-keyed** filename form (review #4) — the form's unit is the
DISTINCT source file, not the entity: `StudentSchedule.txt` (Classes + Enrollments +
`global_config.school_year_sources`), `StudentDemographicInformation.txt`
(Students/Classes/Enrollments), `StaffInformationEnhanced.txt` (Staff/Classes),
`CourseInformation.txt` (Classes/CourseInfo/StudentCourses), etc. One rename propagates
to EVERY reference including `school_year_sources` (whose silent-fallback-to-date-heuristic
would otherwise move every `append_year_to_id` Class ID). Invariant recorded: *no two
references to one role/file may diverge in an emitted overlay*. Dropdowns populated from
actual files in the chosen input folder (+ free text). Delivery/Schedule/finish proceed
unchanged. Post-setup editing: Mapping screen Edit affordance on user-authored configs
only (shipped rows stay read-only); re-activation re-passes the dry-run gate.

**Step machinery (review #1):** the creator step is designed INTO
`src/ui_flet/setup_flow.py`, not around it. Mechanism: a `flow_mode`
(`standard` | `creator`) chosen at `derive_flow`/build time selects between two FIXED
step tuples (the existing `STEP_ORDER` and a creator variant with the FILES step after
FOLDERS) — still no data-driven step engine (the D8 YAGNI stance holds; two constants,
one selector). `step_number`/`next_step`/`prev_step`/`_satisfied_steps`/`TOTAL_STEPS`
become mode-aware through that one selector; the "Step N of M" denominator is the active
tuple's length. **`setup_flow` stays pure (R2-5):** satisfaction facts arrive as INJECTED
`FlowInputs` fields computed by the view — `folders_valid` is the precedent
(`screens/setup.py:474-483`) — so FILES satisfaction ("overlay exists AND verified fact
current") and creator-mode `district_chosen` ("pending token present OR `ws['sis']`")
are each a view-computed bool, and no `AppConfig`/`Path` ever threads into the pure
module (the D8 stance survives contact). `tests/test_ui_flet_setup_flow.py` (114 tests)
extends accordingly; the standard-mode tuple and every existing test stay byte-identical.

**Activation + resume model (reviews #2, R2-1, R2-2, R2-3):**
- The overlay FILE is written (after form validation + a load-back through the real
  `load_config`) at the District step's Continue — per-step persistence holds with no
  control-retaining session state.
- A persisted **pending token** (`creator_pending_sis: str = ""`) is written in the same
  save. A mid-creator abandonment resumes INTO creator mode at the right step (the token
  + the on-disk overlay are the resume memory); a resumed District step in creator mode
  is satisfied by the token, not by `ws["sis"]` (injected as a `FlowInputs` bool — see
  step machinery). Discard in the resumed flow deletes the overlay and clears the token.
- **`sis_type` is written ONLY by the Files-step gate pass** (counts confirmed), which
  also clears the pending token and records the **verified fact**:
  `creator_verified: dict[str, str]` — declared with `field(default_factory=dict)`, the
  `PipelineResult` precedent, since a bare `{}` mutable dataclass default raises at
  class-definition time (round-3 correction 1) — mapping sis id → sha256 of the
  overlay's bytes.
  Hash-keyed is what makes the whole model **fail SAFE**: an edit changes the overlay's
  hash, so even a REFUSED invalidation write leaves the stored hash non-matching — the
  fact never depends on its own write succeeding, and its absence only ever forces
  re-verification, never unlocks anything (this goes into `INVARIANTS.md`).
- **The advisory write path (R2-2, decided):** `_ADVISORY_FIELD_PREFIXES`
  (`app_config.py:97`) gains a REGISTERED third member `creator_` with its rationale
  written into the module comment at `:82-96` in the same change. The membership
  argument, against that comment's own definition ("persisted, but NOT settings that make
  the sync work"): neither field is read by the ETL/CLI/scheduler — the sync runs off
  `sis_type` + the YAML; the token is resume convenience and the fact only gates UI
  activation, and BOTH degrade to "ask again" when lost or refused (the fail-safe
  direction above). They also need exactly the property that motivated the family: a
  write must be refusable on an unreadable profile without trapping the admin. Writer
  shape: the five obligations `identity_save` discharges (write-time
  `settings_unreadable()` re-check · validate key AND value before applying ANY ·
  instance rollback · swallow+log · bool return) are EXTRACTED into a private
  module-level helper; `identity_save` and the new `creator_save` become thin named
  wrappers, each with its own prefix-derived field allowlist — one public writer per
  family (reviewable allowlists preserved), the discipline single-sourced, and
  `identity_save`'s behavior byte-identical under its existing tests. Generalizing
  `identity_save` itself into a prefix-parameterised public writer was rejected: a
  security-adjacent public seam should not gain a parameter that widens what it can write.
- **`creator_verified` read-time hygiene (R2-3):** it is AppConfig's first non-scalar
  persisted field and `_value_fits` checks only the container (`:659-661`), so the fact
  is RE-VALIDATED at read time like every hand-editable field (`stored_identity_email`
  precedent): a key must pass `validate_sis_type`, a value must be 64 lowercase hex —
  anything malformed is treated as ABSENT (= re-verify; fail-safe). Prune rule: every
  `creator_save` drops entries whose sis id no longer resolves to a user-dir file (via
  `resolve_config_path`), so the map is bounded by the count of user-dir configs.
- **`can_finish` in creator mode requires the activation to have happened** (token
  cleared + `sis_type` set) — a creator who never passes the gate cannot flip
  `setup_completed` into the `has_completed_setup() == True` / `is_complete() == False`
  dashboard-with-no-district state the reviewer constructed (`app_config.py:425-444`).
  Pinned both ways (gate blocks finish; passed gate finishes).
- **Mapping's Apply AND the Settings folders-card Save (R2-1)** of a USER-authored config
  check the verified fact and, when absent or stale, route into the gate / refuse with
  plain copy instead of writing `sis_type` (the folders card is the more consequential
  site — its Save also `reconcile()`s the nightly task). Shipped configs are untouched at
  both sites. Both call sites land in S6 (it is one identical check).

**Dry-run gate runner (reviews #7, R2-4, R2-6):** the gate calls
`run_pipeline(dry_run=True)` via `job_runner` on the worker thread — the ONE store-gated
path (`pipeline.py:553-586, 893`; reviewer-verified: `job_runner.route()` already encodes
the `SystemExit`/`Exception` asymmetry, and `_record_early_failure(..., dry_run=…)` is
gated on every early-exit path); it never touches Convert's `_record_manual_run`
direct-write path. **Precise side-effect claims (R2-6):** the gate produces no
run-**store** record — hence no Run History row and no Home repaint (the log parser is
retired) — but it DOES emit the `__DISTRICTSYNC_RUN__` diagnostic log line
(`_log_run_record` is not dry-run-gated, `pipeline.py:891-893`), which is accepted
diagnostics parity, not a leak. And because `DataLoader.__init__` mkdirs the output dir
unconditionally with a CWD-relative fallback on blank (`loader.py:55-61`), **the gate
REFUSES to run without a validated output dir** — no permissive default on a
safety-relevant parameter; the wizard's step order happens to protect it, S6's Mapping
re-host does not, so the refusal lives in the gate itself.

**The missing-expected-column report (R2-4, redesigned):** `src/etl/preflight.py` is a
GENUINELY pure function `({filename: columns}, resolved config) -> report`. It performs
no I/O: the column sets are what the dry-run **already loaded**, surfaced through ONE
additive, defaulted `PipelineResult` field (`input_columns: dict[str, tuple[str, ...]]`,
populated from the frames the extractor returned — a defaulted dataclass field, so no
consumer churn; this deliberately revisits round-1 #7, which rejected pushing
*derivation logic* into `PipelineResult` — raw observed column names are data, and the
derivation stays pure and outside the pipeline). The report's claim is scoped to what is
derivable without duplicating transformer knowledge (a `field_map` entry carries no file
association — `Classes` has five roles and bare column names): *"this expected column,
needed by <entity>, is not present in ANY of your input files"* — which still catches the
pre-processing-district trap the owner named, with no second parse and no extractor seam.
File-level absences are already covered by the gate's existing missing-FILE report.
**Failure-path story (round-3 correction 2, settled at S5's spec):** a run that exits
before `pipeline.py:895` returns NO `PipelineResult`, so the column report is absent
exactly when a transformer validates-at-entry and raises — on that path the trap is
carried by the transformer's own fail-loud actionable message (the repo's standing
column-validation convention), which the gate's error humanization surfaces; preflight's
report covers the RESILIENT path, where blanked cells would otherwise hide the gap. Both
halves pinned together. Any new sink keeps `dry_run` a REQUIRED keyword
(non-negotiable #4).

## Architecture & holistic fit

- **Codebase fit:** new COUNTED `src/config/authoring.py` (overlay model → minimal
  emission → **atomic file write** → load-back verification; imports loader/models, never
  flet — counted, not "pure": it does file I/O) · new pure COUNTED
  `src/ui_flet/config_editor.py` (form state over RESOLVED values: grade-chain model with
  invalid states unrepresentable **post-resolution**, role/file-keyed filename model,
  gate-state derivation, plain-language validation-error humanization) · new pure COUNTED
  `src/etl/preflight.py` (expected-column derivation + report — pure over INJECTED column
  sets, zero I/O; R2-4) · new
  `src/config/bc_district_domains.py` (data module: `{sd_number: (domains…)}` +
  provenance comment — data-as-code so PyInstaller needs no add-data and the
  no-plaintext-email gate is untouched; domains are not addresses) · a new public loader
  seam `resolve_config_path(sis_type, …) -> (path, origin)` (review #9 — `load_config`
  already computes the winning path in `_find_mapping_file:86-103` and discards it; the
  seam feeds S1's WARN-and-drop and S2's badge, and its test seam must exercise BOTH
  search dirs so origin tests are non-vacuous) · view glue in `screens/setup.py` (creator
  branch + Files step) and `screens/mapping.py` (Edit + badge), built via
  `components.py` factories under the `districtsync-design` skill ·
  `mapping_catalog.ConfigSummary` gains `origin` (bundled/user); saves call the existing
  catalog invalidation (`mapping_catalog.py:336-339`) · `setup_flow.py` gains the
  mode-selected step tuple.
- **Product fit:** closes the loop the launch page opened — the not-listed admin now has
  a path that ends in a working sync (`docs/claugentic-PRODUCT.md`; product-designer pass
  at spec time for the creator flow's empty/error states).
- **Quality dimensions to uphold:** `security` (PII: dry-run shows counts only, never
  rows; boundary validation on id/domains/filenames; no new egress) ·
  `data-and-persistence` (atomic write; load-back before commit; never a torn YAML;
  hash-keyed verified fact) · `reliability-resilience` (fail-open catalog preserved;
  **absorbs ROADMAP "Spotted at S3's Stage-7 gate (b)": `district_domains`' hard raise
  becomes origin-aware — raise for bundled, WARN-and-drop for user-dir** — the creator
  makes that latent hazard live, so the floor lands first) · `maintainability-structure`
  (pure modules; screens stay view glue) · `product-ux` (verdict-first, plain language,
  ONE filled primary) · `testing` (no vacuous greens — every "not written/not recorded"
  assertion gets its positive twin) · `api-and-contracts` (the config-schema section of
  `docs/developer/output-contract.md` gains a non-vendor author — noted there; no CSV
  contract change).
- **Future-proofing:** the deferred detailed editor ADDS forms over the same overlay
  model rather than rewriting it; the BC domain table is swappable data (a served
  registry is the recorded future option); `variant_label` (carried from Phase-1
  planning) is adjudicated at spec time — the creator is its second consumer.

## Affected files

- `src/config/authoring.py` — NEW: overlay build/emission/id-derivation/atomic write/load-back.
- `src/config/bc_district_domains.py` — NEW: vendored domain table + lookup.
- `src/etl/preflight.py` — NEW: expected-column derivation + plain-language report.
- `src/ui_flet/config_editor.py` — NEW: form/gate state over resolved values.
- `src/config/loader.py` — `resolve_config_path` seam; origin-aware `district_domains` handling hook.
- `src/config/models.py` — only if the WARN-and-drop needs a validator seam (origin is loader knowledge; prefer the loader).
- `src/ui_flet/setup_flow.py` — mode-selected step tuples, FILES step, satisfaction rules, denominator.
- `src/ui_flet/screens/setup.py` — creator branch (District step), Files step + gate wiring, resume-into-creator, finish precondition.
- `src/ui_flet/screens/mapping.py` — Edit affordance (user configs), origin badge, verified-fact check on Apply.
- `src/ui_flet/mapping_catalog.py` — `ConfigSummary.origin`, badge data, invalidation call sites.
- `src/ui_flet/job_runner.py` — gate job (dry-run + pre-flight) plumbing, incl. the output-dir refusal.
- `src/etl/pipeline.py` — ONE additive defaulted `PipelineResult.input_columns` field (R2-4).
- `src/ui_flet/identity_gate.py` — pins only (SD-regex vs `sd<num>custom`; matched-several on the shared sd48.bc.ca).
- `src/config/app_config.py` — the two `creator_` advisory fields; `_ADVISORY_FIELD_PREFIXES` third member + module-comment rationale; the extracted private write-discipline helper; `creator_save` + read-time validator + prune (R2-2/R2-3).
- `src/ui_flet/components.py` — badge variant only if `status_pill`/`district_chip` can't express it.
- Tests: NEW `tests/test_config_authoring.py`, `tests/test_bc_district_domains.py`, `tests/test_etl_preflight.py`, `tests/test_ui_flet_config_editor.py`; extensions to `tests/test_ui_flet_setup_flow.py` (114 today), `tests/test_config_version_gate.py` (inherited-version pins), catalog/identity/mapping/setup suites.
- Docs: `docs/developer/adding-district.md` (self-service path), `docs/developer/output-contract.md` (config-schema authorship note), `docs/claugentic-PRODUCT_SPEC.md`, `docs/developer/qa-checklist.md` rows, `docs/claugentic-ARCHITECTURE_TREE.md` (new files), `docs/claugentic-INVARIANTS.md` (FOUR entries — see Harness impact), CLAUDE.md subsection, `docs/partner/installation.md` touch-up.

## Research / grounding

- **Files reviewed:** `src/config/loader.py:74-103,161-222,231-277` (cross-dir search +
  `_base` resolution + version gate + the discarded winning path) ·
  `src/config/models.py:446-477,790-810` (EntityConfig `source_files` role dict; the
  deep-merge list-replacement trap) · `config/mappings/myedbc_mapping.yaml:7-9,107-304`
  (grade key space, `school_year_sources`, per-entity roles, shared-file cross-references,
  attendance's headerless special-casing — why it's excluded) ·
  `src/utils/validators.py:30,65-78` · `src/ui_flet/mapping_catalog.py:216-263,290-299,
  336-471` (memo, invalidation, `_PINNED_FIRST`, `filtered_catalog` two-tier rule —
  tier (ii) verified: exactly matching + saved + picked) ·
  `src/ui_flet/identity_gate.py:62,304-316,353-365` · `src/ui_flet/setup_flow.py:56-73,
  170-200` (fixed step tuples, satisfaction, derive_flow) · `src/ui_flet/screens/setup.py:
  461,475,541-543,1198` (mount seed, `district_chosen`, the District-step `sis_type`
  write, the folders-card Save) · `src/etl/pipeline.py:88-94,553-586,893` (PipelineResult,
  the store-gated dry-run path) · `src/ui_flet/screens/convert.py:490-543`
  (`_record_manual_run` — the ungated direct write the gate must avoid) ·
  `src/ui_flet/screens/help.py:11-15` (docs scope-lock — the decoupled roadmap entry) ·
  `src/ui_flet/humanize.py:158-183` (friendly name resolves user-dir configs; except-total).
- **Harness docs consulted:** DECISIONS 2026-07-15 (editor dropped), 2026-07-27 (brief),
  2026-08-05 (creator-priority + re-proposal condition), 2026-08-13 (`row_filters`
  rejection; grade-key semantics), 2026-08-27 (the re-scope this plan implements);
  ROADMAP "SCOPE 2026-08-05", "Spotted at S3's Stage-7 gate (b)", the UNREADABLE-provenance
  hazard entry (2026-07-21); `docs/claugentic-INVARIANTS.md`; CLAUDE.md gotchas (no
  permissive defaults on safety-relevant params; `enabled_entities` via
  `active_entities()`; CEDS OUTPUT space; `grade_to_ceds` non-idempotent).
- **Findings:** everything load-bearing already exists — user-dir search, `_base`
  cross-dir, chain validation on the RESOLVED config, CEDS vocabulary, the store-gated
  dry-run, catalog invalidation, matched-several launch UX (absorbs the non-injective
  domain fact), `_SD_CONFIG_RE` matching the custom id shape, `friendly_district_name`
  resolving user-dir names. Gaps to build: the authoring/emission layer, the forms, the
  step-machinery mode, origin + its loader seam, the domain table, the user-dir domains
  floor, the pre-flight column check, the pending/verified advisory facts.
- **Owner-supplied data (2026-08-27, vendored in S1):** BC district domains CSV — 64 rows
  `District Number,Email,Schools`. Reproduced here so the plan is self-contained:
  5→sd5.bc.ca · 6→sd6.bc.ca · 8→sd8.bc.ca · 10→sd10.bc.ca · 19→sd19.bc.ca ·
  20→sd20.bc.ca · 22→sd22.bc.ca · 23→sd23.bc.ca · 27→sd27.bc.ca · 28→sd28.bc.ca ·
  33→sd33.bc.ca · 34→abbyschools.ca · 35→sd35.bc.ca · 36→surreyschools.ca ·
  37→deltaschools.ca · 38→sd38.bc.ca · 39→vsb.bc.ca · 40→sd40.bc.ca ·
  41→burnabyschools.ca · 42→sd42.ca · 43→sd43.bc.ca · 44→sd44.ca · 45→wvschools.ca ·
  46→sd46.bc.ca · 47→sd47.bc.ca · 48→sd48.bc.ca · 49→sd49.ca · 50→sd50.bc.ca ·
  51→sd51.bc.ca · 52→sd52.bc.ca · 53→sd53.bc.ca · 54→sd54.bc.ca · 57→sd57.bc.ca ·
  58→365.sd58.bc.ca · 59→sd59.bc.ca · 60→prn.bc.ca · 61→sd61.bc.ca · 62→sd62.bc.ca ·
  63→saanichschools.ca + sides.ca + sd63.bc.ca · 64→sd64.org · 67→sd67.bc.ca ·
  68→sd68.bc.ca · 69→sd69.bc.ca · 70→sd70.bc.ca + kackaamin.org · 71→sd71.bc.ca ·
  72→sd72.bc.ca · 73→sd73.bc.ca · 74→sd74.bc.ca · 75→mpsd.ca · 78→sd78.bc.ca +
  sd48.bc.ca · 79→sd79.bc.ca · 81→sd81.bc.ca · 82→cmsd.bc.ca · 83→sd83.bc.ca ·
  84→viw.sd84.bc.ca · 85→sd85.bc.ca · 87→sd87.bc.ca · 91→sd91.bc.ca · 92→nisgaa.bc.ca ·
  93→csf.bc.ca. (School counts dropped — not needed. Cross-checks: SD60=prn.bc.ca and
  SD48=sd48.bc.ca match the shipped configs' claims. **Provenance + quality (owner,
  2026-08-27):** pulled from a public BC doc, grouped by district number, domains taken
  from school contact emails — PLACEHOLDER-quality prefill, not a source of truth. SD78's
  second domain `sd48.bc.ca` is almost certainly a grouping artifact of that method and is
  DROPPED at vendoring, with this note carried into the data module's provenance comment.
  The matched-several pin in Test strategy stays as a general safety net — confirmable
  domains mean domain→district may not be injective — but is not driven by SD78.)

## Risks & mitigations

- **A wrong self-serve config ships a wrong roster (PII).** → the same runtime gates as
  ever (Pydantic at load, `ALLOWED_TRANSFORMS` fail-fast) PLUS the activation dry-run with
  count confirmation + the pre-flight column report; grade keys validated in CEDS space
  with the chain rule on the RESOLVED config; the editor can only produce keys the schema
  already validates.
- **A vendor base change breaks a user overlay at 2 a.m. (review #10 — the overlay's
  sharpest cost; no CI gate ever sees a user-dir config).** → the existing floor is the
  mitigation, verified end-to-end and pinned: a nightly whose config fails to load records
  a FAILED run with the bounded `config` error category (the 2026-07-15 false-green kill
  covers the `run_pipeline` SystemExit sites), so Home shows a red verdict naming the
  problem the next morning — plus the degraded catalog opens Mapping read-only. Pinned by
  a test that a corrupt user-dir overlay produces the `config`-category record, never a
  crash-before-record. An UPGRADE-time proactive check is explicitly NOT built (YAGNI —
  the floor already tells the admin, and the vendor controls base changes and can test
  against the overlay shape this plan freezes in `adding-district.md`).
- **`district_domains` hard raise on a user-dir config kills a nightly sync over a
  presentation key** (pre-existing, ROADMAP S3-gate (b) — made live by this plan). →
  absorbed as S1 scope: origin-aware WARN-and-drop for user-dir configs, raise preserved
  for bundled. Invariant records why the direction may never invert.
- **One renamed file diverging across its references (review #4).** → the Files form is
  role/file-keyed, propagation includes `school_year_sources`, and the no-divergence
  invariant is pinned at the emission layer (an emitted overlay can never contain two
  filenames for one role).
- **Creator abandonment / finish-without-activation (review #2).** → pending token +
  verified fact + `can_finish` precondition, all pinned both ways.
- **Ungated activation paths (reviews #3 + R2-1).** → adjudicated in Goals: all three
  `sis_type` write sites (wizard creator flow · Mapping Apply · Settings folders-card
  Save) gated for user configs via the verified fact; Convert has no write site; the
  honest threat-model sentence bounds what the gate claims; flagged for owner review.
- **Picker collapse under persisted domains (review #8).** → adjudicated in Approach
  (persist; consequence consistent with shipped districts; escape stands; auto-seed
  interplay pinned; CLAUDE.md phrase corrected); flagged for owner review.
- **UNREADABLE-provenance clobber hazard (ROADMAP 2026-07-21 — restated per review #11).**
  The creator writes at surface **(b), the wizard's own District step** (the entry's
  list), and S6's Mapping Edit → re-activate is a NEW writer at surface **(a)** — this
  plan WIDENS the hazard's surface set and each affected slice's spec carries the entry's
  acceptance-test shape (a save under `load_state=UNREADABLE` must not silently blank
  delivery settings). The full merge-onto-re-read fix stays that entry's own item — this
  plan must not make it worse and must note each new writer against it.
- **Wizard pick-path regression (review #12).** → a behavioural positive twin lands with
  S3: the NON-creator District step still persists `sis_type` at `:541-543` and still
  resumes past DISTRICT (not just render smokes).
- **CI/config-count pins.** `available_configs()` count stays 12 (user dir absent in CI);
  tree-check gates the new `src/**/*.py` files; no output-contract change — SD74 golden
  byte-identical through every slice.

## Test strategy

- **Authoring:** emission goldens (a full overlay; an all-defaults overlay that emits only
  identity + `_base`; the chain-companion rule — emitting a narrower grade key forces
  `homeroom_grades` out too); round-trip — emit, then `load_config` against the real
  bundled base resolves and validates; id derivation; atomic write (no torn file on a
  mid-write raise); load-back failure writes nothing; no-`version` emission with the
  inherited-version pin; the role/file no-divergence invariant.
- **Domain table:** total lookup (unknown SD → empty), multi-domain rows (SD63),
  union-with-identity-domain, presumptive fallback, admin-correction path; the SD78
  artifact row (`sd48.bc.ca`) pinned ABSENT from the vendored table; a parity check that
  every vendored domain passes the `district_domains` field validator; matched-several on
  a shared domain kept as a synthetic safety-net case (not SD78-driven).
- **Form model:** grade-chain unrepresentability over RESOLVED seeds; minimal-emission
  mapping; the `"homeroom"` sentinel when timetable scope collapses to empty; CEDS
  vocabulary order from `CEDS_MAPPING` with `"Other"` case preserved.
- **Pre-flight:** pure — expected-column derivation per field-map type over injected
  column sets; "not present in ANY file" report wording; absent-file handling defers to
  the file-level report (never raises into the gate); `PipelineResult.input_columns`
  population pinned (and defaulted-empty everywhere else — additive, no consumer flips).
- **Gate + flow:** dry-run required before activation (positive twin: activation happens
  after pass + confirm); FILES satisfaction + creator `district_chosen` as injected
  `FlowInputs` bools (setup_flow stays I/O-free — asserted structurally); `can_finish`
  blocked pre-gate and open post-gate; no run-STORE record from the gate run — while the
  `__DISTRICTSYNC_RUN__` diagnostic line still emits (both halves asserted together, the
  no-vacuous-greens twin); the gate REFUSES on a blank/unvalidated output dir (and the
  positive twin that a validated dir proceeds); pending-token resume + Discard;
  verified-fact staleness on hand edit (hash mismatch); `creator_verified` read-time
  validation (malformed entry ⇒ absent ⇒ re-verify) + prune on write; `creator_save`
  refusal on an unreadable profile rolls the instance back (the `identity_save`
  discipline, now shared).
- **Catalog/identity:** origin via the two-dir loader seam (NON-vacuous per review #9);
  badge derivation; invalidation; `filtered_catalog` with a user config carrying domains;
  `unmapped_sd_number` suppression; matched-several on the shared domain;
  `friendly_district_name` pin for a user-dir id (review's Q6 answer).
- **Setup-flow machinery:** the 114 existing tests unchanged in standard mode; the
  creator tuple's numbering/satisfaction/denominator; auto-seed does not fire into or
  past a pending creator.
- **Regression:** full suite + SD74 golden byte-identical per slice; render smokes; the
  PII banned-vocabulary sweeps extended to the new screens; the pick-path behavioural twin.

## Decomposition (slices) — reviewer's re-cut adopted

- [x] **S1 — authoring core + domain table + loader seam + user-dir domains floor (no UI).** _(LANDED 2026-09-02 — see the S1 land record at the end of this file.)_
  `authoring.py` (incl. the version rule and the chain-companion emission rule),
  `bc_district_domains.py`, `resolve_config_path` + origin, the origin-aware
  `district_domains` WARN-and-drop, tests. Lands complete: a hand-driven
  `authoring.create(...)` call produces a config the CLI can run — the feature exists
  headless before any form does. (Closes ROADMAP "Spotted at S3's Stage-7 gate (b)".)
- [ ] **S2 — catalog + identity integration.** `ConfigSummary.origin` (through the
  non-vacuous seam), visually distinct rows in all four pickers + Mapping, invalidation
  call sites, `unmapped_sd_number`/matched-state/friendly-name pins, the #8 auto-seed
  pin, the #12 pick-path behavioural twin. Lands complete: a hand-authored user config
  renders distinctly everywhere and the not-listed card retires itself.
- [ ] **S3 — creator flow, activatable end-to-end with inherited filenames.** Creator
  forms (starting point · derived-identity confirm · entities · grades over resolved
  seeds) + `config_editor.py` + the `setup_flow` creator mode + overlay write + pending
  token + **the dry-run gate and the `sis_type` activation write + finish precondition**.
  **Spec order (R2 sizing note): the `creator_` advisory family — prefix registration,
  module-comment rationale, the extracted write-discipline helper, `creator_save`,
  read-time validation, prune — is S3's riskiest element and is spec'd FIRST**, not as a
  by-product of the form work. Uses the INHERITED standard filenames — complete for a
  standard-filename district; a filename-mismatch district is stopped honestly by the
  gate's existing missing-FILE report with no false copy. **Escape hatch if one session
  can't hold it:** split the grades form to S3b (grades default to inherited in S3a);
  both halves stay vertically complete.
- [ ] **S4 — the Files step.** Role/file-keyed filename form, input-dir-aware dropdowns,
  propagation to every reference incl. `school_year_sources`, the no-divergence invariant
  wiring, FILES-step satisfaction. Lands complete: non-standard-filename districts now
  self-onboard.
- [ ] **S5 — pre-flight column check (shrunk per R2-4).** Pure `src/etl/preflight.py` +
  plain-language "not present in any file" report + the additive
  `PipelineResult.input_columns` field, wired into the gate beside the dry-run. Lands
  complete: the pre-processing-district trap is caught at setup, not at 2 a.m.
- [ ] **S6 — Mapping edit + re-gate (+ the folders-card call site, R2-1).** Edit
  affordance on user configs (the second host of the creator forms — the
  `build_setup(on_complete=…)` host pattern is the precedent, settled in S3's spec as a
  design obligation), verified-fact check on Mapping's Apply AND the Settings
  folders-card Save (`setup.py:1198` — one identical check, two call sites),
  edit-invalidates-fact, the gate's output-dir refusal exercised on this host (the wizard
  ordering doesn't protect it here), UNREADABLE-provenance acceptance test for the new
  writer. Lands complete: lifecycle closed for edits.
- [ ] **S7 — export + docs + certification disposition.** Export affordance (reveal/copy
  the file), `adding-district.md` self-service section (freezing the overlay shape the
  vendor tests base changes against), `output-contract.md` authorship note, PRODUCT_SPEC,
  qa-checklist rows for every new surface, ARCHITECTURE_TREE, INVARIANTS (FOUR entries,
  incl. the hash-keyed fail-safe), CLAUDE.md subsection + the "unclaimed" phrase
  correction. **Certification:** the
  2026-08-05 re-proposal condition is discharged as a named closing item — D-0037-6's
  three components (audit pass · product-gap pass · QA walk on a **built exe**) run before
  the release that ships this plan; if the owner defers any component, that is an explicit
  DECISIONS entry, not a silent skip.

---

## Review  _(filled by synthesizer-gate in its plan-gate altitude, Stage 3)_

### Round 1 (2026-08-27) — verdict: CHANGES REQUIRED

_The round-1 findings are preserved in git history (this file, first review commit) and
summarized here; the body above is the author's response revision. Disposition of the 13
required changes:_

1. `setup_flow.py` named + mechanism decided (mode-selected fixed tuples) — **Approach/Affected files/Tests**.
2. Activation model rebuilt (pending token · verified fact · finish precondition) — **Approach**.
3. Activation-scope adjudicated — **Goals**, flagged. *(SUPERSEDED in part by R2-1: the folders-card Save was wrongly called a non-activation here; it is the third gated surface. Convert-has-no-writer stands.)*
4. Files form re-keyed on role/file + `school_year_sources` propagation + invariant — **Approach/S4**.
5. Version rule inverted: emit NO version, inherit the base's — **Approach**, flagged.
6. Grades form over RESOLVED seeds + chain-companion emission rule + CEDS_MAPPING vocabulary — **Approach/Test strategy**.
7. Gate runner named (`run_pipeline(dry_run=True)` via `job_runner`) — **Approach/S5**. *(REFINED by R2-4: derivation stays out of `PipelineResult`, but ONE additive defaulted `input_columns` field now carries the observed column names — raw data, not derivation.)*
8. Domains persistence adjudicated (persist; consequences named; auto-seed pin; CLAUDE.md correction) — **Approach**, flagged.
9. `resolve_config_path` seam + non-vacuous origin tests — **Architecture/Affected files/Test strategy**.
10. Breaking-base-change risk added with the existing-floor mitigation, pinned — **Risks**.
11. UNREADABLE-provenance surfaces restated ((b) + new writer at (a)) — **Risks**.
12. Pick-path behavioural positive twin — **Risks/S2**.
13. Affected files completed — **Affected files**.

Sizing: reviewer's re-cut adopted verbatim (S1–S7, S3b escape hatch). Harness-impact
items (CLAUDE.md subsection, three INVARIANTS entries, Land-time DECISIONS entries,
ROADMAP closure, certification disposition) folded into S7 and the per-slice specs.

### Round 2 (2026-08-27) — verdict: **CHANGES REQUIRED**

A large, substantive improvement — 11 of the 13 dispositions verify clean against code, and
two of them **correct me rather than accommodate me**. Six items remain; one is a factual
error with real consequences, three are unbuildable-as-written, two are wording.

**Verified clean (stated because the author should not have to re-defend these):**
- **#5 (no `version:`)** — `tests/test_config_version_gate.py::TestInheritedVersion::test_version_inherited_from_base_is_gated_in_range` confirms a user config with no `version` inherits the base's and gates in range. Correct call.
- **#7 (runner safety)** — `job_runner.route()` already encodes the `SystemExit`-vs-`Exception` asymmetry in a COUNTED seam (`job_runner.py:110-135`), and `run_pipeline` `sys.exit(1)`s on BOTH `load_config` failure shapes (`pipeline.py:724-747`) — the likeliest gate outcome for a brand-new overlay. The gate cannot silently kill the worker. `_record_early_failure(..., dry_run=…)` is gated on every early-exit path, so no store record either.
- **#10 (breaking-base mitigation)** — accurate: both config-load failures record `RunErrorCategory.CONFIG` and exit 1, so the morning verdict is red and named.
- **#3, Mapping-Apply half** — my round-1 "silently runs the old district nightly" was **wrong**. `mapping_catalog.post_apply_presentation:570-616` renders an assertive (LIVE) or hedged (UNKNOWN + hint) notice naming the old district and routing to Settings→Save. The plan's claim holds.
- **The hash-keyed verified fact fails SAFE** — an edit changes the overlay's hash, so a REFUSED invalidation write still leaves the stored hash non-matching. The fact does not depend on its own write succeeding. That is the strongest single idea in the revision; say it out loud in the plan (see R2-2).

#### Required changes

**R2-1 — The Settings folders-card IS an activation surface; the Goal-2 restatement is
factually wrong.** `_build_settings_folders` (`screens/setup.py:1150-1204`) carries a **live
District dropdown** (`_on_district_change` → `state["sis"]`, options from
`_district_options(_district_catalog(...))` at `:1228-1236`), and its Save writes
`cfg.sis_type = state["sis"]` (`:1198`) **and then calls `reconcile()`, which re-registers
the scheduled task with the new `--sis`**. That makes it the *most* consequential activation
path in the app — strictly worse than Mapping's Apply, which at least surfaces the
stale-schedule notice while this one silently rebakes the nightly. Two clicks activate an
unverified user-authored config directly into the nightly task. (Confirmed there are exactly
three `sis_type` assignment sites: `mapping.py:257`, `setup.py:543`, `setup.py:1198`; Convert
has none — that half of the adjudication is right.) Fix: apply the same verified-fact check
at `:1198` for user-authored configs (route to the gate / refuse with copy; shipped configs
untouched) — it is the identical check S6 already adds, one extra call site — or re-restate
Goal 2 to name this as an accepted hole and flag it for the owner. Add the call site to S6.

**R2-2 — `creator_*` fields cannot be written through the sanctioned path, and the advisory
prefix does not join automatically.** Two facts the plan assumes away: **(a)**
`_ADVISORY_FIELD_PREFIXES` is a hand-maintained 2-tuple (`app_config.py:97`) whose module
comment (`:82-96`) enumerates a per-member justification — a third prefix is an explicit
registration plus a written rationale, not something "settled at spec". **(b)**
`identity_save` is the ONE sanctioned advisory writer and it **refuses any key outside
`_IDENTITY_FIELD_NAMES`** (`:504-508`), which is derived from the `identity_` prefix
(`:625-627`) — so `creator_pending_sis` / `creator_verified` cannot go through it. Decide in
the plan: a sibling choke point discharging the same five obligations (write-time
`settings_unreadable()` re-check · validate key AND value before applying ANY · instance
rollback · swallow+log · bool return), or generalize `identity_save` into a
prefix-parameterised advisory writer — the latter is a change to a security-adjacent seam and
needs its own argument. Also argue family membership against the comment's own definition
("persisted, but NOT settings that make the sync work"): the verified fact *governs which
district converts*, so classifying it advisory is a stretch that must be reasoned, not
assumed. Pair it with the fail-safe hash argument above, which is what makes it defensible.

**R2-3 — `creator_verified` would be AppConfig's first non-scalar persisted field, and the
type check only inspects the container.** `_value_fits`'s parameterised-generic branch is
`isinstance(value, origin)` (`app_config.py:659-661`), so `{"sd51custom": 123}` — or a nested
dict — passes the load-time readability check and reaches the gate. Every hand-editable field
in this codebase re-validates at READ time for exactly this reason (`stored_identity_email`,
`identity_gate.py`). Require: (i) a read-time validator for the fact (key is a valid sis id,
value is 64 hex chars), (ii) a bound/prune rule — an unpruned map grows forever in
`config.json`, and (iii) one honest sentence on the gate's threat model: it prevents an
admin **mistake**, not a hand-edited `config.json` or a hand-dropped YAML (both bypass
everything today). Without (iii) the "cannot become the active district until…" language
over-claims on a trust surface.

**R2-4 — `preflight.py` contradicts itself on purity and cannot derive what the Approach
promises.** **(a)** It is described as "new pure COUNTED" *and* as reading CSV headers "via
the extractor's encoding/delimiter machinery" — incompatible. `extractor.py` exposes no
header-only seam (`load_data:79` / `load_from_bytes:117` parse full frames; no `nrows=0`), so
as written S5 silently adds a seam to a core ETL module that is not in Affected files, and
re-parses every GDE a second time inside the gate (the LIGHT performance tier explicitly says
"watch memory on large GDEs"). **(b)** More fundamental: **a `field_map` entry carries no file
association.** `Classes` declares five `source_files` roles while its field_map names bare
columns (`myedbc_mapping.yaml:167-191`); which frame a column must come from is *transformer*
knowledge, and encoding it in a preflight table would duplicate exactly what the
configurable-columns rule forbids. Fix both at once: make `preflight` a genuinely pure
function `({filename: columns}, resolved config) -> report`, fed by the column sets the
dry-run **already loaded**, and scope its claim to what is derivable — *"this expected column
is not present in ANY of your input files"*. That is honest, still catches the
pre-processing-district trap the owner named, needs no second read, no new extractor seam,
and shrinks S5.

**R2-5 — State FILES/DISTRICT satisfaction as INJECTED `FlowInputs` fields.** "FILES is
SATISFIED when the overlay exists AND its verified fact is current" reads like `setup_flow`
doing I/O. It is expressible purely — `folders_valid` is the precedent (the view computes it
and injects a bool: `screens/setup.py:474-483`) — but say so explicitly, and say the same for
creator-mode `district_chosen` (token-or-`ws["sis"]`, computed by the view). Otherwise the
first implementer threads `AppConfig`/`Path` into the pure module and the D8 stance is lost
on contact.

**R2-6 — Two precision fixes on the gate's side effects (one of them safety-relevant).**
(i) The gate DOES emit a `__DISTRICTSYNC_RUN__` diagnostic line — `_log_run_record` is *not*
`dry_run`-gated (`pipeline.py:891-893`). The true claim is "no run-**store** record, hence no
Run History row and no Home repaint (the log parser is retired)". Reword; the current phrasing
is the kind of small over-claim the honesty lens exists for. (ii) `run_pipeline` constructs
`DataLoader(output_path)` before the dry-run branch, and `DataLoader.__init__` calls
`ensure_directory` unconditionally (`loader.py:55-61`) — a dry-run **creates the output
directory**, and a BLANK output path silently falls back to `Path("data/output")` relative to
CWD. Harmless under the wizard's ordering (Folders precedes Files), but S6 re-hosts this gate
in Mapping where that ordering does not protect it. **The gate must refuse to run without a
validated output dir** rather than inherit the fallback — no permissive default on a
safety-relevant parameter.

#### Sizing / completeness

- **S1 — OK.** Grew by the loader seam + version rule + chain-companion emission rule; still one session.
- **S2 — OK.** Unchanged.
- **S3 — OK with a caveat.** R2-2's advisory-writer decision lands here (first writer) and is
  a security-adjacent seam change — spec it FIRST and treat it as S3's riskiest element, not a
  by-product of the form work. The S3b grades escape hatch remains the right pressure valve.
- **S4 — OK.** Unchanged.
- **S5 — OK, and SHRINKS** if R2-4 is adopted (a pure function over already-loaded column
  sets, no extractor seam, no second parse).
- **S6 — OK.** Gains the `setup.py:1198` call site from R2-1; still one session.
- **S7 — OK.** Unchanged; the certification disposition is now properly named.

#### Harness impact (delta from round 1)

- **`docs/claugentic-DECISIONS.md`** — add the R2-2 adjudication (which advisory-writer shape,
  and why a gate-governing fact is classified advisory) alongside the three already flagged.
- **`docs/claugentic-INVARIANTS.md`** — a fourth entry: *the verified fact is content-hash-keyed
  so a REFUSED invalidation write still fails SAFE* (with the UNREADABLE-provenance provenance).
- **`src/config/app_config.py:82-96`** — the advisory-family module comment IS the contract;
  the third member's rationale goes there in the same change, not only in the plan.
- Everything else from round 1 stands as folded into S7.

### Round-2 author response (2026-08-27) — disposition of R2-1 … R2-6

1. **R2-1** — accepted as a factual error; the folders-card Save is now the third gated
   activation surface (Goals restated; S6 gains the call site; Flags updated).
2. **R2-2** — decided: `creator_` registered in `_ADVISORY_FIELD_PREFIXES` with its
   rationale in the module comment; the five write obligations extracted into a private
   helper; `identity_save` + new `creator_save` as thin named wrappers (one public writer
   per family; generalizing the public seam rejected). Family-membership argument written
   into Approach (nothing the ETL/CLI/scheduler reads; both fields degrade to "ask
   again"; the fail-safe hash property stated out loud, queued as the fourth INVARIANTS
   entry). Flagged.
3. **R2-3** — read-time validator (key `validate_sis_type`, value 64 lowercase hex;
   malformed ⇒ absent ⇒ re-verify), prune-on-write bounded by user-dir config count, and
   the honest threat-model sentence added to Goals.
4. **R2-4** — adopted: preflight is pure over injected column sets; ONE additive
   defaulted `PipelineResult.input_columns` field carries the observed columns (raw data,
   not derivation — the round-1 #7 distinction); report claim scoped to "not present in
   ANY input file"; S5 shrunk. Flagged.
5. **R2-5** — stated: FILES satisfaction and creator `district_chosen` are view-computed
   `FlowInputs` bools (`folders_valid` precedent); setup_flow stays I/O-free, asserted
   structurally.
6. **R2-6** — (i) side-effect claim reworded (no run-STORE record; the
   `__DISTRICTSYNC_RUN__` diagnostic line emits — both halves asserted together);
   (ii) the gate refuses on a blank/unvalidated output dir, exercised on BOTH hosts
   (S3 wizard, S6 Mapping).

Harness-impact deltas folded in: DECISIONS gains the R2-2 adjudication at Land; the
fourth INVARIANTS entry (hash-keyed fail-safe); the `app_config.py:82-96` module comment
carries the third member's rationale in the same change.

### Round 3 (2026-08-27) — verdict: **PASS**

The plan clears the Stage-3 gate. All six R2 dispositions are reflected in the **body**, not
just the table (checked section by section, not taken on faith):

| Item | Where it actually landed |
|---|---|
| R2-1 | Goals `:40-59` (all three sites named with `mapping.py:257` / `setup.py:1198` + the `reconcile()` fact) · Approach `:219-223` · Risks `:401-404` · S6 `:496-503` · Flags `:7` |
| R2-2 | Approach `:189-206` (prefix registration · module-comment rationale · membership argument · extracted helper · two thin wrappers · generalization rejected) · Affected files `:312` · Tests `:450-452` · Flags `:10` |
| R2-3 | Approach `:207-213` · Goals `:53-56` (threat model) · Tests `:449-450` |
| R2-4 | Approach `:240-253` · Architecture `:262-264` · Affected files `:310` · Tests `:438-441` · S5 `:492-495` · Flags `:11` |
| R2-5 | Approach `:165-171` · Tests `:443-444` |
| R2-6 | Approach `:230-238` · Affected files `:309` · Tests `:445-448` · S6 `:501-502` |

**(c) The additive `PipelineResult` field is verified safe — no consumer churn.** There are
exactly **two** construction sites in the repo (`pipeline.py:895` and the stub at
`tests/test_ui_flet_routing.py:51`), **both keyword-only**; every other one of the 34
references is an attribute read, a type hint, or an `isinstance`. The one shape test
(`tests/test_sftp_exit.py:145-156`) asserts via `hasattr`, so an appended defaulted field
cannot turn it red. The round-1 #7 / R2-4 distinction also holds on inspection: #7 rejected
pushing *derivation* into the pipeline's return contract; carrying the extractor's observed
column names is raw data, and the derivation stays pure and outside. Good call.

Two dispositions I want to name as genuinely well-made rather than merely compliant: the
R2-2 family-membership argument (*nothing the ETL/CLI/scheduler reads · both fields degrade
to "ask again" · the write must be refusable on an unreadable profile*) is the right shape of
argument, and extracting the five obligations into a private helper with two thin
prefix-scoped wrappers is a better answer than either option I offered.

#### Binding spec-stage corrections (not optional — carry into the named slice specs)

None of these changes the approach, the slicing, or the risk posture; each is a one-to-three
line fix, which is why they do not hold the gate.

1. **`creator_verified: dict[str, str] = {}` (Approach `:184`) is a mutable dataclass
   default — it raises `ValueError: mutable default` at class-definition time.** It must be
   `field(default_factory=dict)`, as `PipelineResult` itself does (`pipeline.py:91`). Fix the
   plan's signature so an implementer doesn't copy it. → **S3**.
2. **Name which path carries `input_columns` when the run does NOT reach `pipeline.py:895`.**
   The single `PipelineResult` construction is on the success path. A missing *mapped* column
   is row/column-resilient (`apply_field_map` blanks it and records `data_errors`), so the
   dominant pre-processing-district case does reach 895 — but a transformer that validates at
   entry and raises (the fail-loud principle; e.g. `students.py`'s `resolve_column` raise
   under a grade scope) exits earlier, and the column report would then be missing *exactly
   when the trap it exists for fires*. Decide at spec: capture the columns in the gate job
   itself, attach them to the failure path, or explicitly scope the report to the
   run-completed case and let the existing error copy own the raise. → **S5**.
3. **Goal 2 promises the missing-expected-column report, which lands two slices after the
   gate (S3 → S5).** Mark the parenthetical "(lands in S5)" so no S3/S4 copy promises a report
   that isn't there yet — the same no-false-copy discipline the plan applies to the
   inherited-filenames caveat. → **Goals**.
4. **INVARIANTS count drift: "three entries" at `:315` and `:507`, but four are now queued**
   (the round-2 delta's hash-keyed fail-safe entry, which the author's own response at `:671`
   calls "the fourth"). Fix both literals. Small, but this repo has been bitten by exactly
   this class of copied count.
5. **Two round-1 disposition rows now read as current policy while being contradicted by
   round 2** — `:526` ("Convert/folders-Save named non-activations", superseded by R2-1) and
   `:530` ("pre-flight chosen over `PipelineResult` extension", superseded by R2-4). The plan
   file is the durable handoff memory; annotate both rows "— superseded by R2-1 / R2-4" rather
   than leaving a fresh session to discover the contradiction mid-slice. (Approach `:245-247`
   already flags the #7 revisit honestly — this is just the table catching up.)

#### Sizing / completeness

Unchanged and holding: **S1 · S2 · S3 (+S3b escape hatch) · S4 · S5 · S6 · S7** are each
session-sized and land vertically complete. S5 shrank as intended under R2-4; S6 absorbed the
folders-card call site as one identical check at a second call site; S3's riskiest element
(the `creator_` advisory family) is now correctly spec'd first. No slice leaves a half-done
state, and every "not written / not recorded" assertion in the test strategy has its positive
twin.

#### Harness impact

Unchanged from rounds 1+2 and adequately folded into S7 and the per-slice specs: CLAUDE.md
subsection (incl. *no CI gate ever validates a user-dir config* and the "unclaimed → unmatched"
phrase correction) · **four** INVARIANTS entries (role/file no-divergence · bundled-only
domains raise · the creator resume/activation token · the hash-keyed fail-safe) · Land-time
DECISIONS entries for the four flagged adjudications (activation scope · domains persistence ·
version emission · advisory-writer shape) · the `app_config.py:82-96` module comment carrying
the third prefix's rationale in the same change · ROADMAP closure of S3-gate (b) · the named
certification disposition. No new agent required.

**Proceed to Stage 4 (Spec).** The four owner-facing flags (activation scope · domains
persistence · version emission · advisory-writer shape) go to the owner at Stage 5 as written
— they are adjudicated, not open.

---

## Spec  _(per slice, after Review passes — Stage 4)_

_Owner ratified all five flagged adjudications 2026-08-27. Specs are written
just-in-time per slice; S1 below awaits the owner approval gate._

### Slice 1 — authoring core + domain table + loader seam + user-dir domains floor

- **In plain English (the approval gate):** this slice builds the ENGINE, no UI. After
  it lands, a support engineer (or a later slice) can call one Python function with a
  district's facts — SD number, name, domains, starting config, grade choices, entity
  choices, filename renames — and get a small, valid YAML in the app-data `mappings/`
  folder that the existing app and CLI can already run (`--sis sd93custom` works
  immediately). It also makes one safety floor live: a bad domain row in a USER-dir
  config warns and drops instead of killing that district's nightly sync (bundled
  configs keep the loud CI-gated failure). **What "done" means:** the headless
  create→validate→write→run→delete lifecycle works end-to-end against the real bundled
  base, with the emission rules (minimal overlay, no `version`, chain-companion
  homeroom rule, rename propagation incl. `school_year_sources`) all pinned by tests.
  **What you're accepting:** a new authoring layer whose configs no CI gate ever
  validates (the load-back-before-write check is its substitute); the vendored BC
  domain table (placeholder-quality, SD78 artifact dropped) shipping inside the exe;
  and no user-visible change whatsoever in this slice.
- **Files & changes:**
  - `src/config/bc_district_domains.py` (NEW): `DOMAINS_BY_SD: Mapping[int, tuple[str, ...]]`
    (63 rows — the owner CSV minus SD78's `sd48.bc.ca` grouping artifact; provenance
    comment states source, date, placeholder quality, and the drop) ·
    `domains_for(sd_number: int) -> tuple[str, ...]` (TOTAL; unknown → `()`) ·
    `presumptive_domain(sd_number: int) -> str` (`sd<num>.bc.ca`). Data-as-code; no
    PyInstaller change.
  - `src/config/models.py`: one small public predicate
    `is_valid_district_domain(value: object) -> bool` wrapping `_DISTRICT_DOMAIN_RE`
    (single source for the loader pre-screen and, later, the form validation; the
    model validator itself is unchanged).
  - `src/config/loader.py`:
    `resolve_config_path(sis_type: str, *, search_dirs: Sequence[Path] | None = None) -> ResolvedConfigPath | None`
    — a named tuple `(path, origin)` with `origin: Literal["user", "bundled"]`; with
    `search_dirs=None` the real pair `[user_mappings_dir(), bundle_mappings_dir()]` is
    used and origin falls out of which dir won; the test seam takes a two-dir sequence
    whose FIRST element is by contract the user dir — that contract is what makes
    origin tests non-vacuous (review #9). `load_config` gains the user-dir domains
    floor: after `_resolve_inheritance`, when the winning path's origin is `user`,
    invalid `district_domains` entries are DROPPED with ONE counts-only WARN that
    names the consequence ("this district will show in every picker state until the
    row is fixed") and NEVER echoes a value (the model validator's own PII rule);
    bundled configs are untouched — the raise stands. Plus
    `validate_overlay(raw: dict, *, search_dirs=None) -> MappingConfig` — resolves
    `_base` against the standard search dirs, version-gates, and validates, reusing
    the exact `load_config` internals; the authoring load-back calls this BEFORE any
    file exists.
  - `src/config/authoring.py` (NEW, COUNTED — does file I/O):
    `ALLOWED_BASES = ("myedbc", "mbp_all", "mbp_core", "mbponly")` (a widened list is
    a reviewed line, never a parameter) · frozen `OverlaySpec` dataclass (sd_number ·
    district_name · district_domains · base · optional enabled_entities · the three
    optional grade fields (`class_rostering_grades` accepts the `"homeroom"`
    sentinel) · `source_file_renames: Mapping[str, str]` keyed by ORIGINAL base
    filename) · `derive_sis_id(sd_number) -> str` (`sd<num>custom`, through
    `validate_sis_type`) · `build_overlay(spec, *, resolved_base: MappingConfig) -> dict`
    (PURE: minimal emission — emits `sis`, `_base`, `district_name`,
    `district_domains`; omits any value equal to the resolved base; chain-companion
    rule; rename propagation to every entity role AND `school_year_sources`, with the
    no-divergence invariant enforced at emission) · `write_overlay(spec) -> Path`
    (build → `validate_overlay` load-back → `yaml.safe_dump(sort_keys=False)` →
    atomic tmp + `os.replace` into `user_mappings_dir()`; a failed load-back writes
    NOTHING) · `delete_overlay(sis_id) -> bool` (user-dir only — refuses any path
    outside `user_mappings_dir()`).
  - `docs/claugentic-ARCHITECTURE_TREE.md`: entries for the three new modules (the
    pre-commit tree gate enforces same-change updates).
- **In-scope standards dimensions:** `security` (never-echo WARN; domain/id validation
  at every boundary; delete refuses to leave the user dir) · `data-and-persistence`
  (atomic write; load-back before commit; no torn or invalid YAML can reach disk) ·
  `reliability-resilience` (the WARN-and-drop floor fails OPEN — a bad presentation
  row can never kill a nightly; INVARIANTS entry ii records why the direction may
  never invert) · `maintainability-structure` (pure build separated from I/O write;
  single-source domain regex) · `testing` (no vacuous greens — the "writes NOTHING on
  failed load-back" assertion gets a positive write twin).
- **Tests to add:** `tests/test_config_authoring.py` (emission goldens: full overlay ·
  all-defaults overlay emits only identity keys + `_base` + `sis` · chain-companion ·
  `"homeroom"` sentinel · rename propagation incl. `school_year_sources` +
  no-divergence · no `version` emitted with the inherited-version load pin ·
  round-trip through real `load_config` against the real bundled base for each of the
  four `ALLOWED_BASES` · atomic-write crash sim · failed-load-back-writes-nothing +
  positive twin · `derive_sis_id` charset · delete refusal outside user dir);
  `tests/test_bc_district_domains.py` (total lookup · SD63 multi-domain · SD78
  artifact pinned ABSENT · presumptive fallback · every vendored domain passes BOTH
  `is_valid_district_domain` and the model validator — the existing two-regex parity
  convention); loader additions in the existing config test files
  (`resolve_config_path` origin via the two-dir seam — non-vacuous · user-dir
  WARN-and-drop with counts-only log assertion + bundled raise unchanged ·
  `validate_overlay` failure shapes). Full suite + SD74 golden byte-identical.
- **Acceptance criteria:** (1) `write_overlay` for a synthetic SD93 produces a file
  the real `load_config("sd93custom")` loads and `python -m src.main --sis sd93custom
  --dry-run` (under `DISTRICTSYNC_DATA_DIR`) runs; (2) an all-defaults spec emits an
  overlay whose resolved config is byte-equal (via `to_raw_dict`) to its base except
  `sis`/`district_name`/`district_domains`; (3) a user-dir config with one bad domain
  row loads with a WARN and an empty/reduced domain list — the same row in a bundled
  config still raises; (4) no existing test flips; SD74 golden byte-identical; all
  gates green (ruff/format, mypy, bandit, tree-check, email scan, 20-config pin).

### Slice 2 — catalog + identity integration

- **In plain English (the approval gate):** S1 built the engine; this slice makes what it
  writes VISIBLE and HONEST, with no new screens and no new writers. Three effects. (1) A
  config that lives in this computer's DistrictSync folder — added by the admin or handed over
  by support — is **marked** everywhere a district is chosen (wizard District step, Settings
  folders card, Convert, Mapping's switch list) plus Mapping's summary card, so an install
  holding both `sd48myedbc` (shipped) and `sd48custom` (added here) is never a coin flip.
  (2) An added district's email domain now RESOLVES like a shipped one — the launch page
  matches its own admin. (3) Home's "we don't have a mapping for SD93 yet" card **retires
  itself** once an SD93 config exists, shipped or added — no new rule, just a currently
  accidental fact, now pinned. **What "done" means:** a hand-authored overlay renders
  distinctly on all five surfaces, resolves by domain, and silences the not-listed card —
  proven against the REAL two-directory search, not a single-dir fixture. **What you're accepting:** one marker wording beside district names; the
  marker keying on *where the file lives*, so a support-supplied YAML is marked the same way
  (flagged); and Approach #8's consequence becoming observable — a matched custom district
  narrows that admin's pickers to their own rows, exactly as a shipped district already does.
- **Files & changes:**
  - `src/ui_flet/mapping_catalog.py`:
    - `ConfigSummary` gains **`origin: ConfigOrigin`** (the `Literal["user","bundled"]` alias
      imported from `src.config.loader`, never re-spelled). **Required, no default:** a
      defaulted `"bundled"` would let a future construction site silently claim vendor
      provenance for a hand-authored file — the exact claim the marker exists to prevent. Three
      keyword-only construction sites, all in tests, take the field.
    - new private `_origin_of(sis_type, config_dir) -> ConfigOrigin` — **exactly one**
      `resolve_config_path` call per summary (a path lookup, never a second YAML parse),
      computed BEFORE the existing `try` so a config that fails to LOAD still carries its
      origin (a broken overlay is the row most likely to need the marker). Two documented
      rules: an explicit single `config_dir` is `"bundled"`-equivalent, re-using `load_config`'s
      own definition verbatim ("one dir cannot express a tier", `loader.py:498-501`) — which is
      *why* origin tests must go through the real pair; and it is TOTAL (any raise, e.g.
      `user_mappings_dir()`'s mkdir on a locked-down profile, → `"bundled"`) and SILENT, since
      that root cause already surfaces in `summarize_config`'s load WARN and 11 duplicates per
      mount would bury it. Fallback direction argued: an unmarked row loses a distinction; a
      wrongly-`"user"` row would deny shipping a mapping we ship.
    - `_degraded(...)` takes `origin` (passed through, never re-derived).
    - new constant **`CUSTOM_ORIGIN_LABEL = "Added on this computer"`** — the SINGLE source of
      the marker words: PII-free, true whoever authored the file, and no claim about
      editability or vendor work.
    - `disambiguated_labels` (already "the label a picker row renders") appends
      ` — {CUSTOM_ORIGIN_LABEL}` when `origin == "user"`, AFTER the collision-id suffix; the
      marker does not join collision detection (the id suffix already separates a same-named
      pair). **This is the entire four-picker change** — all four render `labels[s.sis_type]`
      (`setup.py:332-333`, `setup.py:1250`, `convert.py:630-634`, `mapping.py:73-76`), so
      **setup.py, convert.py and Mapping's dropdown need no edit**; the picker tests therefore
      assert on rendered trees, not on the helper.
    - `reset_catalog_cache` docstring gains the invalidation rule (below).
  - `src/ui_flet/screens/mapping.py`: `_summary_card` renders
    `components.district_chip(CUSTOM_ORIGIN_LABEL)` beside the district name when
    `origin == "user"`; `_summary_lines` appends a screen-local `CUSTOM_ORIGIN_NOTE` ("This
    mapping lives in this computer's DistrictSync folder — it wasn't shipped with
    DistrictSync."). **The copy says nothing about editing:** Mapping is review-and-switch for
    every row today, a custom row is no different yet, and "read-only for now" would promise
    S6. No new button, so "Use this mapping" stays the one filled primary. Factories only,
    tokens only; the `districtsync-design` skill runs on this change. If that pass rejects
    `district_chip`'s building glyph for a provenance badge, the pre-authorised fallback is
    `components.origin_badge(label)` — the same body, a neutral glyph — and nothing else moves.
  - **Catalog invalidation — the seam decision.** `authoring.write_overlay`/`delete_overlay`
    must NOT call `reset_catalog_cache()`: `src/config/` importing `src/ui_flet/` inverts the
    layer isolation CLAUDE.md states as a principle and drags a UI module into the CLI's import
    graph. **The UI caller invalidates**, right after a successful write/delete. S2 has no UI
    writer (S3 is the first), so it lands the RULE, not a wrapper — written into
    `reset_catalog_cache`'s docstring and into `write_overlay`/`delete_overlay`'s (comment-only,
    no behaviour), with the hazard pinned both ways below. A `note_config_written()` wrapper is
    rejected as dead code with one caller.
  - `identity_gate.py`, `humanize.py`, `screens/home.py`, `screens/setup.py`,
    `screens/convert.py`: **no code change.** `unmapped_sd_number(cfg, available_configs())`
    already counts user-dir ids and `_SD_CONFIG_RE` already matches `sd48custom` (verified:
    `^sd0*48(?!\d)` matches it; `SD4` matches neither); `friendly_district_name` already
    resolves user-dir names; the auto-seed already reads the VISIBLE list. S2's job here is to
    pin behaviour that is currently accidental.
- **In-scope standards dimensions:** `product-ux` (the marker separates two rows that decide
  which roster ships — words, never colour alone) · `maintainability-structure` (one label
  rule for four pickers; origin's single-dir definition re-used from the loader; the
  invalidation seam keeps the config→UI direction) · `reliability-resilience` (`_origin_of`
  TOTAL, degraded rows still carry origin, fallback direction argued) · `testing` (origin is
  non-vacuous only through the real two-dir pair; every "marker shows" has a shipped-row twin
  in the same list) · `privacy` (marker + note are structural facts — no address, path or SD
  number; nothing new logged) · `api-and-contracts` (`ConfigSummary` is the catalog's published
  shape; a required field is a once-only break with three test sites).
- **Tests to add:**
  - `tests/test_ui_flet_mapping_catalog.py` — origin through the REAL pair (autouse
    `isolated_user_profile` gives the tmp user dir; `authoring.write_overlay` puts a real
    `sd93custom` in it): `"user"` for it, **twin** `"bundled"` for `myedbc` (the pair is what
    makes either non-vacuous) · an explicit `config_dir` reports `"bundled"` for a file in that
    dir (the loader's stated rule, asserted not assumed) · a MALFORMED user overlay degrades
    AND still reports `"user"` · `_origin_of` totality (patched `resolve_config_path` raise →
    `"bundled"`, nothing escapes) · `disambiguated_labels`: user row marked, bundled row not,
    a same-`district_name` collision carrying BOTH id suffix and marker · **memo staleness
    both ways** — after `write_overlay` an already-built `catalog()` lacks the new id (the
    documented residual) and after `reset_catalog_cache()` has it (the twin proving the
    invalidation is real, not a comment).
  - `tests/test_ui_flet_filtered_pickers.py` — the marker on screen, one test per picker,
    each asserting the custom row's option text carries `CUSTOM_ORIGIN_LABEL` **and** that a
    shipped row in the same `_texts()` list does not · Mapping's card shows chip + note for a
    custom current mapping and neither for a shipped one · the note promises nothing (substring
    ban on "edit"/"soon"/"later") · new `TestTheNonCreatorPickPathStillWorks` — the **#12
    behavioural twin**: from `setup_completed=False, sis_type=""`, pick + Continue (a) leaves
    the picked id in the isolated profile's on-disk `config.json`, (b) renders the FOLDERS body
    (District dropdown gone), and a fresh `build_setup(page)` lands on FOLDERS — the actual
    persist-and-advance path S3 branches, not a render smoke. Lives here for the mount helpers.
  - `tests/test_ui_flet_identity_resolve.py` — a class over the REAL index
    (`district_domain_index()` after `write_overlay`): a custom overlay claiming `sd93.bc.ca`
    → MATCHED_ONE `("sd93custom",)` · one claiming a SHIPPED district's domain (`sd48.bc.ca` —
    the `sd<num>custom`-beside-`sd<num>myedbc` case the id decision anticipates) →
    MATCHED_SEVERAL with both ids, and `filtered_catalog` returns both rows with only the
    custom one marked · a domain-LESS user overlay behaves exactly like `myedbc` (shown
    unmatched, absent for a matched admin unless saved/picked — the answer to "does an
    unclaimed user config show everywhere?": it does not; the CLAUDE.md phrase fix is S7's).
  - `tests/test_ui_flet_home_identity_cards.py` — the not-listed card RETIRES with an
    `sd93custom` overlay present (`identity_sd_number="93"`), **twin** the same config without
    the overlay still renders it · `sd48custom` retires an SD48 ask while `SD4` does not.
    `tests/test_ui_flet_humanize.py` — `friendly_district_name("sd93custom")` returns the
    overlay's `district_name`; **twin** the raw id once it is deleted.
  - Regression: full suite + SD74 golden byte-identical; `make validate-config` still 20/20
    (test overlays live only in the isolated profile, so the pinned count cannot move).
- **Acceptance criteria:** (1) `origin` is populated on every row of a real `catalog()` build,
  `"user"` exactly for files in `user_mappings_dir()`, proven through the two-dir pair
  (single-dir fixtures asserted to report `"bundled"`); (2) an `sd93custom` overlay renders with
  `CUSTOM_ORIGIN_LABEL` in all four pickers and as chip + note on Mapping's card while shipped
  rows stay unmarked, `screens/mapping.py` the only view file changed; (3) it resolves by domain
  through the real index (MATCHED_ONE alone; MATCHED_SEVERAL beside a shipped district sharing
  its domain) and retires Home's not-listed card, each with its negative twin; (4) the
  invalidation rule is documented at all three sites and pinned stale-then-fresh, with no
  `src/config/` → `src/ui_flet/` import; (5) the non-creator District step still persists
  `sis_type` and still advances/resumes past DISTRICT (behavioural); (6) no existing test flips,
  SD74 golden byte-identical, all gates green (ruff/format, mypy, bandit, tree-check, email
  scan, 20-config pin), `districtsync-design` pass clean.
- **Open questions for the owner:**
  - **Marker wording.** Default `"Added on this computer"`, chosen to stay true when support
    authored the file (see the flag). Alternatives weighed: `"Your district (custom)"` (false
    in that case), `"Custom"` (technical). One constant, one line to change.
  - **Should the marker follow a district OUTSIDE the pickers** — Convert's "This run: <x>"
    pill, page-header district chips, Home's copy? S2 says NO (its job is separating two rows
    at a decision point; a header naming one district has nothing to be confused with), so a
    custom district reads exactly like a shipped one wherever it is merely named. Reversible
    later at the cost of noise on the common path.

_Spec self-check:_
- **Single-lever risk (buildable, but fragile).** `disambiguated_labels` is the ONLY four-picker
  lever; a future picker that stops routing through it, or a designer who wants a non-text badge
  inside a dropdown, silently loses the marker there. The per-picker rendered-tree tests are the
  only guard, and text is a platform constraint (`ft.dropdown.Option(key=,text=)` is the pinned
  0.85.3 form — richer option content is unpinned and the `on_change` class of trap is expensive
  to discover), not a design preference. Named rather than designed around.
- **The required `origin` field is a real, if tiny, contract break.** Five `ConfigSummary(`
  construction sites exist (two in `mapping_catalog`, three in tests); all are keyword-only, so
  the break is three test lines — but it IS a break, chosen over a default that could claim
  vendor provenance falsely. An out-of-repo consumer would see it.
- **One near-vacuous pair, deliberately kept.** The memo-staleness test asserts an ABSENCE (a
  freshly written overlay is missing from an already-built `catalog()`); its post-reset twin is
  what makes it mean anything. If memoisation were ever removed, the stale half would pass for
  the wrong reason — it pins a documented residual, not a guarantee.
- **No S3+ behaviour is promised.** No pending token, no Edit affordance, no activation gate;
  Mapping's note deliberately omits editability. The one hand-off risk is misreading "a user
  config is seed-eligible like any other" as licence to seed INTO a creator flow — S3 owns
  token-aware seed suppression and creator-mode `district_chosen`, and S2 pins only the
  pre-token half (seed reads the VISIBLE list, fires only when nothing is saved, applied AFTER
  `derive_flow`).
- **Layering holds, with one accepted duplicate lookup.** The config layer gains no UI import
  (the UI caller invalidates), and reading a loader `Literal` from a UI module matches the
  existing `filtered_catalog` layer note. `_origin_of` does repeat a path resolution
  `load_config` performs internally — two extra stats per row, no second parse — which is only
  removable by widening `load_config`'s return type; not worth it for a presentation fact.

### Slices 3–7
_Spec'd just-in-time, each after the prior slice lands (S3 next — the creator flow: forms, `config_editor.py`, the `setup_flow` creator mode, the `creator_` advisory family, the dry-run gate and the `sis_type` activation write)._

---

## Land record — S1 (2026-09-02)

- **Commits (branch `claude/plan-0044-implementation-nrdj2z`):** `is_valid_district_domain` predicate · BC domain table (60 districts / 63 domains, both pinned) · `resolve_config_path` + `validate_overlay` + user-dir domains floor · `authoring.py` (OverlaySpec / build_overlay / write_overlay / delete_overlay) · harness docs (INVARIANTS floor-direction entry, ROADMAP (b) closed, DECISIONS) · reviewer fixes.
- **Deterministic gates (local, Linux):** full suite 4,676 passed / 47 skipped / 1 PRE-EXISTING failure (`test_ui_flet_filepicker.py::TestCheckWritable::test_unwritable_dir_is_rejected` — the container runs as root; fails on a clean baseline too) · `ruff check` + `ruff format --check` clean · `python -m mypy src/ --exclude src/ui_flet` clean · bandit clean · `check_no_emails` OK · `make validate-config` 20/20 · SD74 golden byte-identical · `authoring.py` 100% covered.
- **Acceptance:** (1) `write_overlay(SD93)` → `load_config("sd93custom")` → `python -m src.main --sis sd93custom --input tests/snapshots/input --dry-run` exit 0 (renames reproducing SD74's filenames; also pinned in-process with a no-renames twin); (2) all-defaults overlay `to_raw_dict`-equal to its base for all four `ALLOWED_BASES`; (3) user-dir bad domain row → WARN-and-drop, bundled → raise; (4) no existing test flipped.
- **Reviewer sign-off (Stage 7, adversarial architect pass):** 1 BLOCKING fixed (`_require_bare_filename` accepted Windows drive-relative / ADS `:` names, embedded control chars and reserved device names) · 2 SHOULD fixed (`sis` emission — see the flagged deviation; the shipped-domain parity test enumerated the user dir) · 11 NOTEs: 3 promoted to ROADMAP (per-role divergence, rename chains, SD58/SD70 table quality), the rest accepted as documented.
- **CI:** NOT read — `ci.yml` triggers only on push-to-main / PRs to main. Owed at PR time (flag above).
- **Housekeeping landed alongside:** `.githooks/pre-commit` executable bit (the tree gate had never run on Linux/macOS) · a web `SessionStart` hook installing the toolchain.
