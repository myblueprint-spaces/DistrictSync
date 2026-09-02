# 0044 — District self-service config editor (Phase 2, re-scoped)

- **Status:** S1 · S2 · S3 · S4 LANDED on `claude/plan-0044-implementation-nrdj2z` (2026-09-02; each Stage-7 reviewed). Owner tested locally 2026-09-02 → S6 REORDERED ahead of S5 (+ a Mapping CREATE door), a Files-step clarity fix in flight; then S5, S7
- **Resumable from:** Stage 4/6 — the S6 spec (Mapping hosts the creator; re-gate on Apply + folders-card Save) then its implementation; S5 and S7 follow. Owner go given 2026-09-02
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
  - `S3 staleness + activation writer: chose a digest of the RESOLVED config (sha256 over the validated `MappingConfig.model_dump(mode="json")`) over the plan's overlay-BYTES hash — a vendor base change on app update leaves overlay bytes unchanged while what converts differs — plus an advisory `authored_with` root key (app version + base digest) the loader ignores; and ONE atomic activation writer (`AppConfig.activate_creator_config`), which makes `sis_type` write sites four, not three` — reviewable at the S3 gate.
  - `S6 scope addition (owner, 2026-09-02): a CREATE door — "Set up a district that isn't listed" — on the Mapping screen, because a configured install otherwise has no way into the creator; S6 reordered ahead of S5` — ratified by the owner in the same message.
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
- [x] **S2 — catalog + identity integration.** _(LANDED 2026-09-02.)_ `ConfigSummary.origin` (through the
  non-vacuous seam), visually distinct rows in all four pickers + Mapping, invalidation
  call sites, `unmapped_sd_number`/matched-state/friendly-name pins, the #8 auto-seed
  pin, the #12 pick-path behavioural twin. Lands complete: a hand-authored user config
  renders distinctly everywhere and the not-listed card retires itself.
- [x] **S3 — creator flow, activatable end-to-end with inherited filenames.** _(LANDED 2026-09-02 — grades form INCLUDED, S3b hatch not taken.)_ Creator
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
- [x] **S4 — the Files step.** _(LANDED 2026-09-02.)_ Role/file-keyed filename form, input-dir-aware dropdowns,
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

### Slice 3 — creator flow, activatable end-to-end with inherited filenames

- **In plain English (the approval gate):** S1 built the engine, S2 made its output visible; this
  slice gives it a FRONT DOOR. An admin whose district isn't listed picks "Set up my district" on
  the wizard's first step, confirms four things on forms (which shipped mapping to start from ·
  district name/number/email domains · which CSVs to produce · which grades are rostered), then on a
  creator-only **"Your files"** step presses one button that runs a **test conversion writing
  nothing**, shows how many rows each CSV would hold, and only THEN offers "Use this district". That
  press is the ONLY thing here that makes the new district the one this install converts; everything
  before it is a file in the admin's own DistrictSync folder that changes nothing. Abandon halfway
  and the wizard reopens where you were, with a way to discard. **What "done" means:** a district
  shipping NO mapping today can be set up, tested and activated in-app, with the nightly then
  running it — on the base's STANDARD MyEd BC filenames (a district whose export renames files is
  stopped honestly by the test's missing-file list; S4 adds the rename form). **What you're
  accepting:** three new persisted values (a resume token, a record of what was tested, and — S3's
  addition — the app version + base fingerprint written INTO the overlay); a test run that creates
  the output folder and writes a diagnostic log line but no Run History row; and the plan's honest
  limit — the gate stops an admin *mistake*, not a hand-edited settings file.

#### 3.1 The `creator_` advisory family (spec'd FIRST — S3's riskiest element)

- **Registration.** `_ADVISORY_FIELD_PREFIXES` (`app_config.py:97`) gains `_CREATOR_FIELD_PREFIX =
  "creator_"`, and the module comment at `:82-96` gains its rationale in the SAME change (that
  comment IS the contract — R2-2). The sentences, argued against the comment's own definition
  ("persisted, but NOT settings that make the sync work"): *"`creator_` — the in-progress state of a
  self-service district (plan 0044). Nothing in the ETL, CLI or scheduler reads either field: the
  sync runs off `sis_type` + the YAML. The token is resume convenience; the tested-fact only decides
  whether the UI OFFERS activation, and it is keyed on a digest of the RESOLVED config, so losing it
  can only force another test run — never unlock one. Both degrade to 'ask again', the property that
  put `identity_` here: the write must be refusable on a profile we failed to read without trapping
  the admin."* What the prefix does NOT cover: `activate_creator_config` (3.1.4), which writes
  `sis_type` beside them and is deliberately non-advisory.
- **The two fields** (additive, safe defaults — a v3.9.x `config.json` loads unchanged):
  `creator_pending_sis: str = ""` and `creator_verified: dict[str, str] =
  field(default_factory=dict)` — never `{}`, which raises `ValueError: mutable default` at
  class-definition time (round-3 correction 1; `pipeline.py:91` is the precedent).
- **3.1.1 The extracted write-discipline helper.** `AppConfig._guarded_field_write(self, updates:
  dict[str, object], *, allowed: frozenset[str], refuse_when_unreadable: bool, subject: str) ->
  bool` discharges the five obligations `identity_save` documents at `:455-501`: (1) validate KEY
  (membership in `allowed` — never `hasattr`, which would let `identity_save=…` shadow the bound
  method) AND VALUE (`_value_fits` against `_settings_field_types()`) for every update before any
  `setattr`; (2) re-check `settings_unreadable()` on THIS instance at write time, before any
  mutation, when `refuse_when_unreadable`; (3) apply, then `save()`; (4) swallow + log
  `SettingsOverwriteRefused`/`OSError`; (5) `_restore` the snapshot on ANY failure and return a
  bool. `refuse_when_unreadable` is REQUIRED keyword-only with no default
  (`write_overlay(overwrite=)`, `_store_run_record(dry_run=)`). `subject` is the log noun, so
  identity's WARNING strings stay byte-identical.
- **3.1.2 The wrappers.** `identity_save` and the new `creator_save(**updates) -> bool` become thin
  named wrappers, each passing its own prefix-derived allowlist (`_IDENTITY_FIELD_NAMES` at
  `:625-627`; a new `_CREATOR_FIELD_NAMES` derived the same way from `fields(AppConfig)`), both
  `refuse_when_unreadable=True`; `creator_save` also prunes (3.1.5). `identity_save`'s public
  behaviour — raise shapes, messages, returns, rollback, log text — stays byte-identical under
  `tests/test_app_config_identity.py` (25 tests, unchanged). Generalising the public seam stays
  rejected (R2-2).
- **3.1.3 The digest is over the RESOLVED config, not the overlay's bytes** (S3 correction to
  Approach `:184`, flagged): a vendor base change on app update leaves the overlay bytes — and a
  byte-keyed hash — UNCHANGED while what converts differs, so the gate would never re-fire.
  `authoring.resolved_digest(config: MappingConfig) -> str` = sha256 over
  `json.dumps(config.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
  ensure_ascii=False)`, lowercase hex. Whole validated model, not a subset: `to_raw_dict()` is the
  trap (only `mappings` + `global_config`, so `district_domains`/`version` changes are invisible),
  and a hand-maintained "fields that matter" list is a second spelling that will drift. Cost named:
  a cosmetic `district_name` fix also invalidates — the safe direction, since over-firing only
  re-asks while under-firing activates something untested. Fail-safe both ways: an edit to the
  overlay OR the base moves the dump, and a REFUSED invalidation write leaves a non-matching stored
  digest, so the fact never depends on its own write succeeding. `authored_with` (3.1.6) is
  invisible to it (`extra="ignore"`), so re-writing provenance cannot self-invalidate. Shape is
  validated at ONE boundary — `validators.is_config_digest(value: object) -> bool` (TOTAL, 64
  lowercase hex) — read by both `app_config` (deferred import, its existing pattern) and
  `config_editor`; `app_config` must NOT import `authoring`, which would drag yaml + pydantic +
  loader into the settings module. Effectful companion `authoring.current_digest(sis_id) -> str |
  None` loads through the real `load_config` and digests it; TOTAL — any load failure is `None`,
  which can only mean "not current".
- **3.1.4 Activation is ONE save through a third named wrapper.**
  `AppConfig.activate_creator_config(*, sis_type: str, digest: str) -> bool` — allowlist exactly
  `{"sis_type", "creator_pending_sis", "creator_verified"}`, `refuse_when_unreadable=False`,
  validating `sis_type` (`validators.validate_sis_type`) and `digest` (`is_config_digest`) before
  applying anything. Rejected: routing through `creator_save` (it must refuse non-`creator_*` keys
  or become the `sis_type` back door obligation 3 exists to prevent); TWO saves (a torn state where
  the district is active while the token still stands — and the resumed flow's Discard would then
  delete a LIVE config); a bare `cfg.sis_type = …; cfg.save()` as at `setup.py:550` (drops
  validation + rollback on the write that matters most). Non-advisory *because* it carries a chosen
  setting: `sis_type` off its default is what `_carries_chosen_settings` (`:377-407`) counts, so
  `save()`'s UNREADABLE branch — write, after quarantining the predecessor — applies exactly as for
  the standard District step. That parity is the claim and the UNREADABLE-provenance acceptance
  test's subject. **Record at land:** `sis_type` write sites go three → FOUR — verified today at
  `setup.py:550` (wizard), `setup.py:1213` (folders card; the plan cites `:1198`, moved),
  `mapping.py:293` (Apply; the plan cites `:257`, moved), plus this gated one. Goals' "exactly
  three" is restated at S3 land; the invariant it stood for is unharmed, since the new site is the
  gated one.
- **3.1.5 Read-time validation + prune.** `creator_verified` is AppConfig's first non-scalar
  persisted field and `_value_fits`'s generic branch checks only the CONTAINER
  (`app_config.py:661-662`), so `{"sd93custom": 123}` loads clean. (i) **Read — pure, no I/O, in
  `config_editor.py`** (the `identity_gate.stored_identity_email` precedent):
  `stored_verified_digest(cfg, sis_id) -> str | None` returns the stored value only when the map is
  a dict, the KEY passes a total `validate_sis_type` wrapper and the VALUE passes
  `is_config_digest`; anything malformed (wrong type, nested dict, 63 chars, uppercase) reads as
  ABSENT ⇒ re-test. Deliberately NOT gated on `authoring.is_custom_sis_id`: S6 gates activation on
  ORIGIN (`ConfigSummary.origin == "user"`, S2's landed rule), so a support-handed user-dir override
  of a shipped id (`sd40myedbc`) must stay activatable after passing the same gate. Companion
  `verified_is_current(stored, current) -> bool` = both present and equal (two `None`s never read as
  "fine"). (ii) **Prune** on every `creator_save` and `activate_creator_config`: drop entries whose
  id no longer resolves via `loader.resolve_config_path(id)` to a `"user"` file, bounding the map by
  the user-dir config count. Deferred import inside the method, and TOTAL — any raise prunes nothing
  and never blocks the write.
- **3.1.6 `authored_with` — provenance IN the overlay (S3 addition, flagged).** `MappingConfig`
  declares `extra="ignore"`, so a root key the loader never reads is free and durable.
  `build_overlay(spec, *, resolved_base, authored_with: Mapping[str, str] | None = None)` emits it
  verbatim when given and omits it when `None` — so `build_overlay` stays PURE (no version lookup,
  no file read) and S1's emission goldens pass untouched; `write_overlay` ALWAYS supplies it via the
  pure `authoring.authored_with(resolved_base, *, app_version) -> dict[str, str]` = `{"app_version":
  …, "base": <id>, "base_digest": resolved_digest(resolved_base)}`, with the version from
  `src.utils.version.app_version()` (THE version lookup — build-stamped `src/_version.py`, else
  installed metadata, else `"dev"`; `version.py:28-40`). The `None` default is legitimate because
  provenance is ADVISORY: activation safety is 3.1.3's digest, so this key can never permit a run.
  `_ROOT_KEY_ORDER` gains `authored_with` LAST. Read back by `authoring.read_authored_with(sis_id)
  -> AuthoredWith | None` (frozen dataclass; reads the YAML through `resolve_config_path`, since the
  validated model DROPS the key; TOTAL → `None`). **Surfacing:** the pure
  `config_editor.overlay_staleness(authored_with, *, running_version, current_base_digest) ->
  StalenessFact` (`version_differs`, `base_changed`) lands in S3 WITH its caller — the Files step's
  note, where an admin resuming a district set up by an older build needs to know why another test
  run is asked for. The Mapping-card / picker note is an **S6 obligation** (S6 owns that card's copy
  and its Apply gate); S3's emission + reader make it view glue there. Existing coverage cited, not
  duplicated: a user-dir config declaring an OLD `version:` already hits the loader gate (different
  major → raise, `loader.py:234-241`; newer minor → WARN, `:242-250`), and an overlay with no
  `version` inherits its base's (`test_config_version_gate.py::TestInheritedVersion`).

#### 3.2 `config_editor.py` (NEW — pure, COUNTED, no flet)

- **Form state over RESOLVED values.** Frozen `CreatorForm` + `with_*` returns (a step can never
  half-mutate shared state): `base` (one of `authoring.ALLOWED_BASES`, rendered through
  `BASE_LABELS: Mapping[str, str]` — plain-language names declared HERE so no picker shows a raw
  id), `sd_number`, `district_name`, `domains`, `entities` (seeded from the resolved base's
  `enabled_entities` over the seven `authoring.CREATOR_ENTITIES`; `StudentAttendance` absent by
  construction), and the grade fields.
- **Domains:** `derive_domains(identity_domain: str, sd_number: int)` = the stored identity domain
  (`identity_gate.stored_identity_domain`, computed by the view) ∪
  `bc_district_domains.domains_for(sd)`, order-stable and de-duplicated; ONLY when both are empty,
  `(presumptive_domain(sd),)`. Correctable; every kept/entered value passes
  `models.is_valid_district_domain` (S1's single source) at the boundary, and an invalid entry is
  reported WITHOUT echoing the value (the likeliest bad paste is a personal address —
  `OverlaySpec.__post_init__`'s existing rule).
- **Grades — invalid chain states unrepresentable POST-RESOLUTION by construction, not by
  validation.** Vocabulary and ORDER derive from `CEDS_MAPPING`'s values
  (`tuple(dict.fromkeys(CEDS_MAPPING.values()))` — `CEDS_GRADE_CODES` is an unordered frozenset and
  `"Other"` is its one mixed-case member, so nothing case-normalises). The form asks ONE question —
  "which grades are rostered?" (`rostered`) — plus "which of those get a homeroom class instead of a
  timetable?" (`homeroom`, offered ONLY from `rostered`). `to_overlay_spec()` derives
  `student_rostering_grades = rostered`, `class_rostering_grades = rostered` (or
  `models.CLASS_ROSTERING_HOMEROOM_SENTINEL` exactly when `homeroom == rostered`) and
  `homeroom_grades = homeroom`, so `homeroom ⊆ class ⊆ student` holds by derivation and
  `check_rostering_grade_scopes` (`models.py:699`) can only confirm it. Rejected: three independent
  multi-selects validated on Continue — that makes the admin reconstruct a chain rule from an error
  message. Untouched fields emit `None` (minimal emission stays the authoring layer's job);
  `source_file_renames={}` in S3.
- **Gate state:** `GateState = NOT_RUN | RUNNING | PASSED | FAILED | REFUSED_NO_OUTPUT_DIR` + frozen
  `GateOutcome(state, counts, missing_files, note)`, derived by `gate_outcome_for(...)` from
  injected facts (the worker's `PipelineResult` or exception, the output-dir-valid bool, the
  expected/present filename lists). `humanize_config_error(exc) -> str` maps
  `validate_overlay`/`load_config` failures to a BOUNDED category (bad grades / bad domain / missing
  base / unreadable / other) — never the raw message, since Pydantic errors carry values and a
  domain must never be echoed. The missing-file list derives pure from
  `pipeline.advisory_expected_files(config)` vs the folder's GDE names (Convert's precedent,
  `convert.py:1414-1480`) — never scraped from log text.

#### 3.3 `setup_flow.py` — creator mode (two fixed tuples, one selector; still no step engine)

- `SetupStep` gains `FILES = "files"`; `FlowMode = Literal["standard", "creator"]`. `STEP_ORDER`
  UNCHANGED; `CREATOR_STEP_ORDER = (DISTRICT, FOLDERS, FILES, DELIVERY, SCHEDULE, FINISH)` — six:
  FILES after FOLDERS because the gate needs the input folder, DELIVERY still before SCHEDULE (F1 —
  `--sftp` is baked at registration). One selector `step_order(mode)`; `_PRE_FINISH_STEPS` →
  `_pre_finish_steps(mode)`; `TOTAL_STEPS` stays the module constant (`== 5`, pinned by
  `TestStepScaffolding`) with a mode-aware companion `total_steps(mode)` for the "Step N of M"
  denominator; `step_number`/`next_step`/`prev_step` gain `*, mode: FlowMode = "standard"` —
  keyword-only with the standard default, so every existing call and all 116 tests stay
  byte-identical.
- **Four NEW injected `FlowInputs` fields, each defaulting to the SAFE value and consulted only in
  creator mode** (so no default can loosen standard mode): `mode: FlowMode = "standard"`,
  `creator_district_chosen: bool = False` (a pending token is present, or a form has been written),
  `files_step_satisfied: bool = False` (the overlay exists AND `verified_is_current(stored,
  current)`), `creator_activated: bool = False` (`sis_type` is the creator id AND the token is
  cleared). The VIEW computes all of them — `folders_valid` (`setup.py:481-491`) is the precedent —
  so **no `AppConfig`, `Path`, `authoring` or `config_editor` import enters `setup_flow`**, asserted
  structurally by an AST import test in the shape of `test_ui_flet_identity_gate.py:328-342`,
  extended to ban `pathlib`, `src.config` and `config_editor` alongside `flet`.
- `_satisfied_steps` adds FILES (creator only, from `files_step_satisfied`) and in creator mode
  reads `creator_district_chosen` for DISTRICT. `can_advance` gates FILES on `files_step_satisfied`
  (NOT skippable — it IS the activation gate). `derive_flow` in creator mode ANDs
  `creator_activated` into `can_finish`, so a creator who never passes the gate cannot flip
  `setup_completed` into the `has_completed_setup() == True` / `is_complete() == False` state the
  reviewer constructed (`app_config.py:425-444`). `FlowState` gains NO field — the `set(vars(state))
  == {"resume_step","satisfied","can_finish"}` pin (`test_ui_flet_setup_flow.py:432`) stays green by
  design.
- **S3 ships FILES as the GATE step ONLY.** Body: the inherited standard filenames as READ-ONLY text
  (the resolved config's `source_files`, grouped by file, no inputs) · the missing-file list · the
  gate button · the counts · the activation confirm. S4's filename FORM lands in this same step
  ABOVE the gate, so S4 adds controls and moves nothing, and `files_step_satisfied` keeps its
  meaning.

#### 3.4 `screens/setup.py` — creator branch, Files step, resume, discard

- **Mount.** `_mount_wizard` reads `pending = cfg.creator_pending_sis.strip()` FIRST. Non-blank +
  `authoring.overlay_path(pending).exists()` ⇒ `mode="creator"`, form state rehydrated from the
  overlay (`load_config` + `read_authored_with`), and **the D9 auto-seed at `setup.py:503` is
  SKIPPED** (the #8 obligation): seeding a bundled district into a pending creator flow would
  satisfy the District step with the wrong answer and resume past the step the admin is mid-way
  through. A token whose overlay is GONE clears itself via `creator_save(creator_pending_sis="")`
  and falls back to standard mode.
- **District step, standard branch untouched** — plus one text-tier `CREATOR_ENTRY_LABEL = "Set up
  my district"` under the dropdown (text tier keeps the step's ONE filled primary), prefilled from
  `identity_sd_number` when the launch page stored one (`identity_gate.sd_number_digits`). The
  non-creator path (`_forward` → `cfg.sis_type = ws["sis"]; cfg.save()`, `:549-551`) is unchanged —
  S2's `TestTheNonCreatorPickPathStillWorks` (#12 twin) must stay green.
- **The creator forms live on the District step**, one card sequence in the step body (starting
  point → identity confirm → entities → grades), built ONLY via `components.py` factories +
  `tokens`, verdict-first, ONE filled primary ("Continue"). `ft.Dropdown` uses `on_select`;
  `ft.Checkbox`/`ft.Switch` use `on_change`; `ft.TextField` uses `helper`
  (`docs/FLET_1.0_CONVENTIONS.md:33-34`). There is no checkbox factory today — the seven entity rows
  use bare `ft.Checkbox` with token colours, and the `districtsync-design` pass decides whether that
  earns a `components.check_row(...)` (either outcome is one reviewed line).
- **Continue on the creator District step IS the write**, in order: `form.to_overlay_spec()` →
  `authoring.write_overlay(spec, overwrite=True)` (its load-back refuses anything invalid before
  bytes land) → `cfg.creator_save(creator_pending_sis=sis_id)` →
  `mapping_catalog.reset_catalog_cache()` (S2's rule — the UI caller invalidates, right after a
  successful write) → advance. A raised write shows `CREATOR_WRITE_FAILED_NOTE` +
  `humanize_config_error(exc)` and advances NOTHING; a `False` from `creator_save` shows
  `CREATOR_TOKEN_REFUSED_NOTE` and still advances (the file is on disk and re-visitable — only
  resume convenience was lost). `overwrite=True` is right here and only here: a creator re-visiting
  their own step is the edit case that flag exists for. **Discard** (secondary, on every creator
  step): `delete_overlay(sis_id)` → `creator_save(creator_pending_sis="")` → `reset_catalog_cache()`
  → re-mount standard mode with `CREATOR_DISCARDED_NOTE`; a `False` from `delete_overlay` is
  idempotent success, not an error.
- **The gate.** `GATE_RUN_LABEL = "Run a test conversion"` is the step's ONE filled primary until it
  passes; then per-entity counts and either `GATE_PASSED_HEADLINE` + `GATE_CONFIRM_LABEL = "Use this
  district"` (now the filled primary, the run button demoting to secondary `GATE_RERUN_LABEL`) or
  `GATE_FAILED_HEADLINE` + bounded copy. It runs through `JobRunner` exactly as Convert's
  `_start_convert` (`convert.py:749-802`), so `route()`'s `SystemExit`-vs-`Exception` asymmetry
  covers `run_pipeline`'s `sys.exit(1)` config paths (`pipeline.py:726-748`); `on_error` logs the
  trace and renders category copy only. **`creator_gate_job(sis_id, *, input_dir, output_dir) ->
  PipelineResult` lives in `src/ui_flet/job_runner.py`** (the plan's stated home) with a DEFERRED
  `from src.etl.pipeline import run_pipeline` so nothing new loads on a UI mount; being COUNTED is
  what makes the refusal testable headless — **it raises `GateRefused` when the output path is blank
  or `filepicker.validate_output_dir(output_dir).ok` is False**, because `DataLoader.__init__`
  mkdirs unconditionally and falls back to a CWD-relative `data/output` on blank
  (`loader.py:55-61`), and the wizard's step order protects this while S6's Mapping host will not
  (R2-6). It calls `run_pipeline(..., dry_run=True, source="manual")` and NEVER Convert's
  `_record_manual_run` (`convert.py:492`), the one ungated store writer.
- **Activation.** Confirm → `digest = authoring.current_digest(sis_id)` →
  `cfg.activate_creator_config(sis_type=sis_id, digest=digest)` (ONE save: `sis_type` set, token
  cleared, `creator_verified[sis_id] = digest`, map pruned) → `reset_catalog_cache()` → advance. A
  `None` digest or a `False` return keeps the admin on the step behind
  `CREATOR_ACTIVATE_FAILED_NOTE` ("nothing was changed — please try again"), never a silent advance.
  `_STEP_TITLES` gains `FILES: "Your files"`, `_BODIES` gains `SetupStep.FILES`, `_step_header`
  reads `total_steps(mode)` so the denominator says 6. `_finish` is unchanged (save-then-verify,
  `FINISH_SAVE_FAILED_NOTE`, the `on_complete` host seam); the finish precondition sits upstream in
  `can_advance(FINISH, …)`.
- **New copy constants** — module-level, one per string, PII-free, plain language, and screened
  against the banned identity vocabulary: **"verify"/"verified" IS banned**
  (`DESIGN_SYSTEM.md:40-47`), so every gate string says *test / test run / checked*.
  `CREATOR_ENTRY_LABEL`, `CREATOR_START_TITLE`, `CREATOR_START_PROMPT`, `CREATOR_IDENTITY_PROMPT`,
  `CREATOR_DOMAINS_HELPER`, `CREATOR_DOMAIN_INVALID_NOTE`, `CREATOR_ENTITIES_PROMPT`,
  `CREATOR_GRADES_PROMPT`, `CREATOR_HOMEROOM_PROMPT`, `FILES_STEP_TITLE`, `FILES_INHERITED_NOTE`,
  `FILES_MISSING_NOTE`, `GATE_RUN_LABEL`, `GATE_RUNNING_CAPTION`, `GATE_PASSED_HEADLINE`,
  `GATE_PASSED_DETAIL`, `GATE_FAILED_HEADLINE`, `GATE_REFUSED_NO_OUTPUT_NOTE`,
  `GATE_STALE_VERSION_NOTE`, `GATE_STALE_BASE_NOTE`, `GATE_CONFIRM_LABEL`, `GATE_RERUN_LABEL`,
  `CREATOR_DISCARD_LABEL`, `CREATOR_DISCARDED_NOTE`, `CREATOR_WRITE_FAILED_NOTE`,
  `CREATOR_TOKEN_REFUSED_NOTE`, `CREATOR_ACTIVATE_FAILED_NOTE` (+ `BASE_LABELS` in `config_editor`).
  **No string mentions a column report (S5), an edit affordance (S6) or a filename form (S4).**

#### 3.5 Gate side effects (R2-6) · and the S6 host obligation

- The gate writes **no run-STORE record** on any path — `_store_run_record`'s `dry_run` gate
  (`pipeline.py:552-586`) plus `_record_early_failure(..., dry_run=…)` cover success, failure and
  every early exit — hence **no Run History row and no Home repaint**. It DOES emit the
  `__DISTRICTSYNC_RUN__` diagnostic line (`_log_run_record` is not dry-run-gated,
  `pipeline.py:879-880`) — accepted parity, not a leak. No CSVs, no anomaly check, no SFTP
  (`pipeline.py:818-849`); it DOES create the output directory, which is why the refusal exists.
  **Both halves are asserted in ONE test** (no store row + the log line present) — the
  no-vacuous-greens twin.
- **The host seam.** The creator forms must be mountable by a SECOND host in S6 (Mapping's Edit), so
  S3 factors them behind `build_creator(page, *, cfg, sis_id, form, on_written, on_activated,
  on_discarded) -> ft.Control` — the same shape and reasoning as `build_setup(page, *,
  on_schedule_changed, on_complete)`: the host owns what happens after the payoff, the surface owns
  the work. S3 wires the wizard host only; naming the callbacks now is what stops S6 becoming a
  second wizard. Pinned by a test that the wizard passes exactly these callbacks and that
  `build_creator` renders identically from both mounts (the `_finish` two-mount equivalence
  precedent).

#### In-scope standards dimensions

`security`/`privacy` (counts only, never rows; no domain, name or path in any log or bounded error
copy; id/domain/digest validated at every boundary; no new egress) · `data-and-persistence` (one
atomic settings save per act; S1's load-back-before-write; the digest keyed on the RESOLVED config
so an inherited change cannot go unnoticed) · `reliability-resilience` (every new read TOTAL and
failing toward "test it again"; a refused advisory write rolls the SHARED instance back; the catalog
stays fail-open) · `maintainability-structure` (one write-discipline helper + three reviewed
allowlists; pure form/gate/flow logic outside the view; `setup_flow` I/O-free, asserted) ·
`product-ux` (verdict-first, ONE filled primary per step, plain language, no promise of S4/S5/S6) ·
`testing` (a positive twin for every negative) · `observability-ops` (a counts-only INFO line naming
the id and entity counts, never a district name).

#### Tests to add

- `tests/test_app_config_creator.py` (NEW): three advisory prefixes registered AND the module
  comment names `creator_` (source-substring parity — the "literal copied out of `src/`" rule) · a
  creator-only save REFUSED under UNREADABLE with the instance rolled back, **twin** a readable
  profile persists · a non-`creator_*` key refused loudly, incl. `sis_type` (the back-door pin) · a
  wrong-typed value raises BEFORE any write, and a bad key late in a multi-field call leaves no
  partial mutation · `False` + rollback on `OSError` / `SettingsOverwriteRefused` ·
  `activate_creator_config` writes all three fields in ONE `save()` (patched call count == 1),
  validates `sis_type`/`digest` first, and — the deliberate asymmetry — is NOT refused under
  UNREADABLE while the predecessor bytes ARE quarantined (the ROADMAP 2026-07-21 acceptance shape:
  `sftp_host` recoverable from the `config.corrupt-*.json` copy, nothing silently blanked) · prune
  drops a vanished id, **twin** keeps a live one, and a raising `resolve_config_path` prunes nothing
  and still writes. `tests/test_app_config_identity.py` unchanged (the shared-helper equivalence
  proof).
- `tests/test_config_authoring.py` (EXTEND): `resolved_digest` stable across two loads; DIFFERENT
  for a changed `district_domains` (the `to_raw_dict`-only trap) and for an edited BASE in a tmp
  bundled dir, **twin** unchanged base ⇒ identical · a re-write that only bumps `authored_with`
  leaves the digest UNCHANGED · `write_overlay` ALWAYS emits `authored_with` carrying the running
  `app_version()` + base digest, **twin** `build_overlay` without provenance emits no such key (S1
  goldens unchanged) · `read_authored_with` totality (absent / not-a-dict / wrong value types /
  unreadable ⇒ `None`) · `is_config_digest` accept/reject table in `tests/test_validators.py`.
- `tests/test_ui_flet_config_editor.py` (NEW): domain derivation (identity ∪ table; presumptive ONLY
  when both empty; de-dup + order; SD63's multi-domain row) · an invalid domain reported without
  echoing it · entity seeding from each of the four bases · grade derivation fed through the REAL
  `check_rostering_grade_scopes` via `validate_overlay` for every reachable form state (incl.
  `homeroom == rostered` ⇒ the sentinel, and `homeroom == ()`) · CEDS order + `"Other"` case ·
  `to_overlay_spec` emits `None` for untouched fields · `gate_outcome_for` table incl.
  `REFUSED_NO_OUTPUT_DIR` · `humanize_config_error` bounded categories with a substring ban on a
  planted domain + path · `stored_verified_digest` hostile-value table (non-dict, bad key, 63 chars,
  uppercase, nested) ⇒ `None`, **twin** a well-formed entry returns it · `verified_is_current`
  refuses two `None`s · `overlay_staleness` both flags and neither · missing-file derivation,
  **twin** a complete folder reports none.
- `tests/test_ui_flet_setup_flow.py` (EXTEND — the 116 existing tests byte-identical): the creator
  tuple + six-step denominators · `step_number(step, mode="creator")` · FILES satisfaction from the
  injected bool only · creator DISTRICT satisfied by `creator_district_chosen` · `can_finish`
  BLOCKED with FILES satisfied but `creator_activated=False`, OPEN with both · `can_advance(FILES)`
  closed then open · the purity AST test.
- `tests/test_ui_flet_creator_flow.py` (NEW, view glue): the creator branch renders and Continue
  writes overlay + token + calls `reset_catalog_cache` (spy), **twin** a failed load-back writes
  nothing and does not advance · resume from a token lands in creator mode at the right step, and a
  token with no file self-heals to standard · Discard deletes + clears + invalidates · the D9 seed
  does NOT fire while a token is pending, **twin** it still fires with no token and one visible
  district · the gate REFUSES a blank output dir with NO `run_pipeline` call (spy), **twin** a
  validated dir calls it once with `dry_run=True` · a gate run writes NO `history.db` row while the
  `__DISTRICTSYNC_RUN__` line IS logged (one test, both halves) · activation writes `sis_type` +
  clears the token + records the digest in one save, and a hand-edit of the overlay re-closes the
  FILES gate (hash staleness) · `can_finish` blocked pre-gate / open post-gate through the real
  mount · the banned-vocabulary sweep (`test_ui_flet_identity_page.py:32-50`'s `BANNED_WORDS` +
  `BANNED_DESCRIPTIONS`, word-boundary matched, with its existing falsification twin) over EVERY new
  copy constant and every creator/Files body · no rendered string contains "column", "edit", "soon"
  or "later".
- Regression: full suite + SD74 golden byte-identical; `make validate-config` still 20/20 (test
  overlays live only in the isolated profile); render smoke covers the new step bodies.

#### S3b escape hatch (a concrete cut line)

S3a ships everything above EXCEPT the grades form: `config_editor` keeps `CreatorForm`'s
`rostered`/`homeroom` fields and the derivation, but no view builds them and `to_overlay_spec()`
returns all three grade fields `None` (fully inherited — a correct config for the K-12 districts the
base was written for). Moving to S3b: the `_creator_grades_body` card,
`CREATOR_GRADES_PROMPT`/`CREATOR_HOMEROOM_PROMPT`, exactly the grade rows of
`tests/test_ui_flet_config_editor.py` (the chain-derivation table) and the grade assertions in the
creator-flow mount test. Nothing in the step machinery moves — every form sits on the DISTRICT step
— so both halves stay vertically complete and S3b is additive view glue over an already-tested
derivation.

#### Acceptance criteria

1. On a profile with no SD93 config an admin completes District → "Set up my district" → the four
   forms → Continue (an overlay exists in the profile's `mappings/`, the token is stored, the
   catalog is invalidated) → Folders → Your files → test conversion → counts → "Use this district" →
   Delivery/Schedule → Finish; afterwards `sis_type == "sd93custom"`, `creator_pending_sis == ""`,
   `creator_verified["sd93custom"] == authoring.current_digest("sd93custom")`, `setup_completed`
   True.
2. The finish line is UNREACHABLE until activation: `can_finish` False with every other step
   satisfied and `creator_activated=False`, True once activated (pinned both ways).
3. The gate refuses without a validated output dir (no `run_pipeline` call) and with one runs
   exactly once with `dry_run=True`, writes no store row, and emits the `__DISTRICTSYNC_RUN__` line.
4. Staleness re-fires the gate: with `sd93custom` active, an edited BASE (or a hand-edited overlay)
   makes `verified_is_current` False and re-closes the FILES step; an unchanged base leaves it
   current (twin). A malformed `creator_verified` entry reads as absent.
5. Every `write_overlay` output carries `authored_with` with the running `app_version()` + base
   digest; `read_authored_with` is TOTAL; the digest is insensitive to that key.
6. `identity_save` is byte-identical (its 25 tests unchanged); `creator_save` is refused on an
   UNREADABLE profile with the instance rolled back; no wrapper can write outside its own allowlist.
7. All 116 `test_ui_flet_setup_flow.py` tests unchanged; `setup_flow` imports no `flet`, `pathlib`,
   `src.config` or `config_editor`; S2's `TestTheNonCreatorPickPathStillWorks` green.
8. No new copy promises S4/S5/S6 behaviour and none carries banned identity vocabulary; all gates
   green (ruff/format, mypy, bandit, tree-check, email scan, 20-config pin), SD74 golden
   byte-identical, `districtsync-design` pass clean, `ARCHITECTURE_TREE` updated for
   `src/ui_flet/config_editor.py` in the same change.

#### Open questions for the owner

- **An admin at a district we DO ship (SD48) presses "Set up my district".** Default: ALLOW —
  `sd48custom` is written and coexists with `sd48myedbc` (the id decision anticipates it, and S2's
  marker + `disambiguated_labels` keep the rows distinguishable); the starting-point card names the
  shipped mapping as the recommended choice FIRST, because in that case it usually is the right
  answer. Rejected alternative: refuse when `resolve_sd_number` finds a shipped config — that blocks
  the district whose export legitimately differs from the shipped assumption, the case support hits
  most.
- **Where the "written with an older version — run the test again" note appears.** S3: the Files
  step only (where the re-test is being asked for). S6: the Mapping card / picker note. Should Home
  carry it too? Default NO — Home answers "did the roster sync?", and a config that still converts
  is not a fault.
- **`GATE_CONFIRM_LABEL` wording:** "Use this district" (default) vs "Start using this district" vs
  "Make this my district". One constant, one line.

_Spec self-check:_
- **Riskiest element: `activate_creator_config` as a fourth `sis_type` writer that is deliberately
  NOT refused on an unreadable profile.** It is the right call (it carries a chosen setting, so
  parity with `setup.py:550` is the honest posture), but it adds a writer to the
  UNREADABLE-provenance hazard, and its acceptance test proves only that the predecessor is
  quarantined — not that delivery settings survive inside `config.json`. Named, not solved; the
  merge-onto-re-read fix stays that ROADMAP entry's item.
- **The digest choice trades clicks for safety, knowingly.** Digesting the whole validated model
  means a district-name typo fix re-closes the gate. Over-firing beats under-firing, but if the
  owner reads it as friction the narrower subset (`mappings` + `global_config` + `district_domains`
  + `version`) is a two-line change with a drift risk I would rather flag than hide.
- **`build_creator`'s second host is unexercised until S6.** S3 pins the callback shape and a
  both-mount render equality, so the seam's value is asserted by construction rather than by use —
  the `on_complete` precedent carried the same gap for exactly one slice.
- **Nothing promises S4+.** FILES ships gate-only with read-only filenames; no string says "column"
  (S5), "edit" (S6) or "rename" (S4); the missing-file list derives from `advisory_expected_files`,
  which exists today. The substring bans are the guard.
- **Layering holds, with two accepted couplings.** `job_runner` gains a DEFERRED `src.etl.pipeline`
  import (COUNTED — which is what makes the output-dir refusal testable headless), and
  `config_editor` imports `src.etl.transformers.grades` for the CEDS vocabulary — the same problem
  `models._ceds_grade_codes` documents; if that import proves circular at implementation the fix is
  `models`' deferred-import pattern, never a copied table.
- **Vacuous-green watch:** "no store row", "no `run_pipeline` call" and "the seed does not fire" are
  all ABSENCE assertions; each is specified with its twin in the same test or the adjacent row, and
  the vocabulary sweep inherits the existing falsification test that the word list is matched, not
  merely iterated.

#### S3 review ledger (Stage 7, foundations pass 2026-09-02 — carried into the S3 land record)

Reviewed: S2 + S3 α/β/γ (commits `3902eac..81f43ec` + fixes). **No BLOCKING finding in the reviewed code**; one BLOCKING relayed to δ in flight (bandit B105 on a copy constant whose NAME carried `TOKEN` — rename, no nosec). Fixed at once: `CreatorForm.__post_init__` now refuses an explicit empty `rostered` (a direct construction bypassed `with_rostered`'s guard and emitted `class_rostering_grades: []`, which `humanize_config_error` mislabelled as a chain problem); the `config_editor` purity test bans `pathlib` too. **Owed once δ lands (components.py is δ's to touch until then):** fold `origin_badge` and `district_chip` onto one private `_chip(label, *, icon)` — the badge duplicates the chip's whole body. **Recorded, not fixed:** (1) `authoring.authored_with(resolved_base, *, base, app_version)` deviates from the spec's `(resolved_base, *, app_version)` — correct, a resolved `MappingConfig` carries no `_base` id — and the module function is shadowed by `build_overlay`'s `authored_with=` parameter (harmless today; a future line inside `build_overlay` would bind the parameter). (2) `resolved_digest` is ORDER-sensitive on `district_domains` (`sort_keys` orders dicts, never lists) — kept: a reorder is an authoring edit and over-firing only costs a re-test. (3) `can_advance(FILES, <standard>)` returns False while `step_number(FILES)` raises in standard mode — False is the safe answer; asymmetry noted in the docstring. (4) `read_authored_with` has no size cap on the YAML it reads — the same alias-bomb exposure `load_config` already has on the same file in the admin's own profile. (5) `humanize_config_error` maps `FileNotFoundError` → "missing base" by TYPE; a missing GDE reaches it only as the pipeline's `SystemExit` (→ OTHER), so the mapping is honest exactly because `run_pipeline` converts file errors — a dependency worth one docstring sentence.

**δ (view glue) pass, 2026-09-02:** two BLOCKING, both reproduced on the real mount and FIXED in the follow-up commit — (1) un-ticking "Use my starting point's grades" seeded `rostered = homeroom = <base>.homeroom_grades`, so OPENING the question wrote a K-7-only roster with the sentinel (fix: seed `rostered = CEDS_GRADE_ORDER`, `homeroom = <base>.homeroom_grades`); (2) correcting the SD number on Continue wrote the new id without deleting the old overlay — `93 → 94` left BOTH in `mappings/`, both in every picker, and Discard's "Nothing was kept" became false (fix: delete the superseded id after the successful write). SHOULD, fixed alongside: Finish pressed end-to-end in the acceptance-1 test; the creator block moved to `screens/creator.py` (wizard → creator dependency, ready for S6's host); `split_domains`/`sd_number_from_text` moved to `config_editor` with direct tests (the 4-digit bound is safety-relevant); `components.check_row` factory for the ~50 hand-rolled checkboxes; one spelling of the FILES-satisfied fact; `visible_ids` computed lazily (creator mode no longer pays the catalog build). **Recorded, not fixed:** the gate's single-flight guard is per-`build_creator` instance, so Run → Back → Continue → Run yields a second concurrent dry run whose orphaned result paints a detached tree (harmless: dry-run writes nothing); `route` catches `SystemExit`/`Exception` only, so a hung `run_pipeline` latches "Testing your files…" until the admin navigates away (pre-existing, shared with Convert); `_on_base` discards entity ticks on a starting-point change and `_on_sd` does not re-render, so `creator_shipped_note` appears one render late; `creator_gate_job` validates the OUTPUT dir only — a blank input dir reaches `run_pipeline` and surfaces as FAILED (correct, nothing is created); §3.4's "Discard on every creator step" holds for the creator's own two surfaces only (FOLDERS renders Back/Browse/Continue) — the plan sentence, not the build, was imprecise. **Security/PII, spec conformance and the design system were clean** on the reviewer's planted-value probes (path + domain + column name in a worker exception never reached the rendered tree; exactly one filled primary in all five states).

### Slice 4 — the Files step: role/file-keyed filename form

#### In plain English (the approval gate)

S3 ships "Your files" as a read-only list plus the test-conversion gate: a district using the
standard MyEd BC filenames self-onboards today, one whose files are named differently is stopped
honestly with nothing it can do about it. S4 adds the one thing that district needs — **beside
each file DistrictSync looks for, the name their extract actually uses** — picked from the files in
their input folder or typed in. The unit is the FILE, not the entity: setting `StudentSchedule.txt`
to `studentcourseselection.txt` once fixes Classes, Enrollments **and** the school-year lookup
together (SD74's real shape). **Done means:** an admin whose extract looks like
`tests/snapshots/input/` gets an overlay in which every reference to a renamed file moved with it,
a missing-file list answering against their names, and a test conversion that passes first try.
**What you're accepting:** one more write through the same `write_overlay` load-back (no new
persisted field — S4 writes NOTHING to `config.json`), and that changing a file name after
activation re-closes the gate until the test is run again. **Two limits stay open**, both on
ROADMAP: one file delivered under two names per ROLE has no expression (the map is keyed by
original filename), and the absent-mapped-column report is S5's.

#### 4.1 `config_editor.py` — the pure model (the view only assembles it)

- **`FILE_LABELS: Mapping[str, str]`** — a plain-language name per distinct base filename (8 rows),
  falling back to the filename itself. DATA, not derived: `ClassInformationEnh.txt` split on
  capitals reads "Class Information Enh", and this is admin-facing copy. A parity test pins a row
  for every distinct file of all four `ALLOWED_BASES`.
- **`SourceFileSlot`** (frozen): `original` · `label` · `references: tuple[tuple[str, str], ...]`
  (entity, role — KEYS; `setup._entity_label` owns the vocabulary) · `names_school_year: bool`.
- **`distinct_source_files(resolved_base, *, expected) -> tuple[SourceFileSlot, ...]`** — walks
  `resolved_base.mappings` in `CREATOR_ENTITIES` order (then any remaining entity key, sorted, so
  nothing is silently dropped), keeps sites whose entity is in `active_entities()` and whose
  filename is in `expected`, de-duplicating by filename first-seen. `expected` is INJECTED so
  `pipeline.advisory_expected_files` (which the view already computes, and which already narrows a
  fully homeroom-scoped config's inert `student_schedule`/`class_info` roles) stays the single
  source for "which files matter". `names_school_year` is set when
  `global_config.school_year_sources` also names the file; a school-year file no ACTIVE entity
  reads gets NO slot — `extract_required_files` never loads it, so the row would offer a rename
  that changes nothing. **Order is this walk, never `expected`'s:** `advisory_expected_files`
  returns `list(set)`, whose order varies with `PYTHONHASHSEED`, and S4 replaces S3's inherited
  instability with a deterministic row order.
- **`FileFormRow`** (frozen; composition, not duplication): `slot` · `effective` (the renamed name
  else the original) · `present`. Built by **`file_form_rows(slots, *, renames, present)`**, reusing
  `missing_files`' case-INSENSITIVE fold so `studentcourseselection.txt` never reads absent against
  `StudentCourseSelection.txt`. *Deviation from the brief:* no separate `present_files(...)` — it
  would duplicate that fold to return its own input.
- **`CreatorForm.renames: Mapping[str, str] = field(default_factory=dict)`** +
  **`with_rename(original, new)`**: strips `new`, DROPS the entry when blank or equal to `original`
  (a "rename" to the standard name is not one, and the emission must stay minimal), else validates
  through `authoring.validate_source_filename` (§4.2). A blank `original` raises; an unknown
  `original` is not rejected here (the form only passes slot originals, `_build_renames` fails loud
  at write time, and the model holds no resolved base). `__post_init__` re-validates every entry,
  so a direct construction cannot bypass `with_rename` (the `rostered` precedent).
  `to_overlay_spec()` passes `self.renames` unless its existing `source_file_renames=` keyword
  overrides it, so S3's call sites stay identical.
- **`renames_from_resolved(resolved_base, resolved_current) -> dict[str, str]`** — the RESUME
  inverse: where the current config names something else at a base reference site, record
  `base_name → current_name`, first-seen per original, same walk. TOTAL. A hand-edited DIVERGENT
  overlay collapses to its first-seen name and the next Save writes it consistently — a visible
  repair, never a silent loss, because the rows on screen and the written map are ONE value.
- **`files_primary_action(*, unsaved, passed, already) -> Literal["save","run","confirm","none"]`**
  — lifts S3's in-view three-way branch into pure code and adds the fourth state, so "exactly ONE
  filled primary" is asserted over all eight combinations rather than read off a render. `unsaved`
  wins: a test against names the config on disk lacks reports on the wrong files.
- **No `suggested_rename(...)`** — a folder holding both spellings has no derivable answer, and a
  guessed pre-selection nobody is told about is the silent default D9 ruled against. YAGNI.

#### 4.2 `authoring.py` — two boundary lines (closes ROADMAP S1-review item 2)

- **`validate_source_filename(value, *, label="file name") -> str`** — a PUBLIC thin wrapper over
  `_require_bare_filename`, so the form validates one field without building an `OverlaySpec` and
  the product keeps ONE filename boundary. Every S1 call site unchanged.
- **`OverlaySpec.__post_init__` gains the chain refusal:** a rename TARGET may not also be a rename
  ORIGINAL. `{"A.txt": "B.txt", "B.txt": "C.txt"}` clears unknown-original, target-collision and
  no-divergence today, and A's role then reads the base's B file — wrong data with every guard
  green. Compared **case-folded** (`b.TXT`/`B.txt` are one file on Windows), and the same fold goes
  onto `_build_renames`' two-originals-onto-one-target check, which is case-sensitive today and
  would let `a.txt`/`A.txt` collapse two roles onto one Windows file: one private `_folded_name`,
  two call sites, strictly-more-refusals. **Reachable from the form** — the dropdown offers folder
  files and a folder may hold another slot's standard name — so the view pre-checks it with its own
  sentence instead of leaving `CONFIG_ERROR_OTHER` to answer "can't be used as it stands".

#### 4.3 `screens/setup.py` — the form ABOVE the gate (S3 moves nothing)

- Inside `build_creator(..., stage="files")` the file card becomes ONE ROW PER SLOT, above every
  gate control, in `distinct_source_files` order: the label + standard name (read-only) · a
  `ft.Dropdown` (`on_select`; one option per file in `_folder_filenames(cfg.input_dir)` — ALL files
  via S3's helper, since a rename target is whatever the district delivers and Convert's
  `_GDE_SUFFIXES` is a display nicety, not a boundary) with `FILES_KEEP_STANDARD_LABEL` first and
  the effective name selected · a `ft.TextField` (`helper=`) for a name the folder does not hold ·
  `components.FileChip(effective, present=row.present)` · a "used for" caption from
  `row.slot.references` through `_entity_label`, plus the school-year clause when
  `names_school_year`, because that propagation silently moves every `append_year_to_id` Class ID.
  **`ft.Dropdown(editable=True)` exists on 0.85.3 and is DECLINED:** typed text lives in
  `text`/`on_text_change` while `value` stays the selected option's key, nothing here exercises it,
  and filenames should not rest on an unproven two-field contract. One line into
  `docs/FLET_1.0_CONVENTIONS.md` records that plus `Dropdown.helper_text` vs `TextField.helper` —
  the only S4 doc change; S7 owns the rest.
- **ONE write action: `FILES_SAVE_LABEL` ("Save these file names"), SECONDARY tier** — every write
  load-backs through the real loader, so per-row writes would run it on each keystroke. Order:
  pending map → `to_overlay_spec()` → `write_overlay(spec, overwrite=True)` →
  `reset_catalog_cache()` → `on_files_saved`. Cheap local problems answer FIRST and attempt no
  write (`FILES_NAME_INVALID_NOTE` · `FILES_NAME_DUPLICATE_NOTE` for two rows on one name ·
  `FILES_NAME_IS_STANDARD_NOTE` for the chain shape); an authoring refusal reuses
  `CREATOR_WRITE_FAILED_NOTE` + `humanize_config_error`, never an exception string.
- **S4 writes NOTHING to `AppConfig`** — no token (re-storing it post-activation would flip
  `creator_activated` back to False), no `sis_type`, no explicit `creator_verified` invalidation.
  The stored digest simply stops matching, `_creator_gate_current` goes False and the step
  re-closes: **the hash-keyed fail-safe working**, since the fact never depends on its own write
  succeeding and its absence can only force a re-test. `GATE_RESAVED_NOTE` says so when a save
  lands on an already-active district.
- **`on_files_saved: Callable[[CreatorForm, str], None]` — a FIFTH required keyword callback** on
  `build_creator`, not a stage-conditional reuse of `on_written` (which advances a step; a callback
  whose meaning depends on the host's step is how a second wizard starts). The host sets
  `ws["creator_form"]`, clears `ws["files_ok"]`, re-renders FILES in place; S6's Mapping host needs
  the identical callback.
- **ONE presence source:** rows, chips and the missing-file list all derive from the PENDING
  effective names, so nothing on screen contradicts anything else; the gate's own
  `GateOutcome.missing_files` (the config on disk, after a run) stays the authoritative report.
- **Mount-time memo:** the resolved base and `expected` are I/O and cannot change while the step is
  on screen, so each resolves ONCE per mount into `st` (the S4b "one memoised config read per
  mount" cost note; `_resolved_base` is not memoised today).
- **Copy re-read.** `FILES_INHERITED_NOTE`'s "the standard MyEd BC names your starting point uses"
  becomes FALSE once a row is renamed → replaced by `FILES_INTRO_NOTE` ("DistrictSync looks for
  these files in your input folder. If your district's files are named differently, set the name
  yours uses beside each one."). `FILES_MISSING_NOTE` stays TRUE (it asserts absence, not naming)
  and gains the now-available fix as its tail. New: `FILES_INTRO_NOTE` ·
  `FILES_KEEP_STANDARD_LABEL` · `FILES_TYPED_NAME_LABEL` · `FILES_USED_FOR_PREFIX` ·
  `FILES_SCHOOL_YEAR_CLAUSE` · `FILES_SAVE_LABEL` · `FILES_SAVED_NOTE` · `FILES_UNSAVED_NOTE` ·
  `FILES_NAME_INVALID_NOTE` · `FILES_NAME_DUPLICATE_NOTE` · `FILES_NAME_IS_STANDARD_NOTE` ·
  `GATE_RESAVED_NOTE`. **No admin-facing string uses "column" (S5), "edit" (S6) or even "rename"**
  — the copy says what a file is *called* — so S3's `FORBIDDEN_PROMISES` stays byte-identical and
  keeps guarding both later slices.

#### In-scope standards dimensions

`security` (every typed filename crosses the ONE public boundary — traversal, drive-relative, ADS,
control chars, reserved devices — and the chain refusal closes the last way a fully validated map
still reads the wrong file) · `privacy` (notes name the failed CHECK, never the typed value; logs
stay counts-only) · `data-and-persistence` (one write path, load-back before bytes land, no new
persisted field) · `reliability-resilience` (every derivation TOTAL, fail-safe toward "test it
again") · `maintainability-structure` (pure slots/rows/tiering in `config_editor`, assembly in the
screen, one filename boundary in `authoring`) · `product-ux` (one filled primary in all eight
states, one presence source, deterministic order) · `testing` (a positive twin for every absence
and refusal assertion).

#### Tests to add

- **`tests/test_ui_flet_config_editor.py`** — slot derivation for EACH of the four `ALLOWED_BASES`
  (count, order, `references`; `names_school_year` true for `myedbc`/`mbp_all`'s
  `StudentSchedule.txt`, the slot ABSENT for `mbponly`, whose active entities never read it) ·
  order pinned against a hand-written tuple + a twin that a shuffled `expected` cannot change it ·
  `FILE_LABELS` covers all four bases' distinct files, fallback returns the filename ·
  `with_rename` drops on blank / whitespace-only / identical-after-strip, keeps a stripped value,
  refuses each `validate_source_filename` shape (traversal, `:`, control char, reserved device,
  >255) · `__post_init__` re-validates a directly constructed `renames` · `file_form_rows` presence
  is case-insensitive both ways (twin: a genuinely absent file reads absent) · `to_overlay_spec()`
  carries `renames`, the explicit keyword still wins · `renames_from_resolved` round-trips a written
  SD74-shaped overlay and answers `{}` for an unrenamed one (twin) · `files_primary_action` over all
  8 combinations, exactly-one-primary as a property.
- **`tests/test_config_authoring.py`** — the chain refusal (`A→B`, `B→C`) with its case-folded twin
  (`A→b.TXT`, `B.txt→C`) and the positive twin that SD74's real four-rename map still writes and
  loads · the case-folded duplicate-target refusal (`a.txt`/`A.txt`) beside the existing exact one ·
  `validate_source_filename` is the same boundary as `_require_bare_filename` (one sweep, both
  entry points).
- **`tests/test_ui_flet_creator_flow.py`** (extends S3's mount helpers) — one row per distinct file
  with its standard name and caption · dropdown options == the folder's files, keep-standard first ·
  **the headline flow:** an `sd93custom` overlay with NO renames, activated over
  `tests/snapshots/input`, then `StudentSchedule.txt` set to `studentcourseselection.txt` (+ the
  other three) → "Save these file names" → the WRITTEN overlay moves `Classes`, `Enrollments` **and**
  `global_config.school_year_sources` → `verified_is_current` flips False and the FILES footer closes
  (twin: True immediately before) → the REAL gate re-run over those inputs passes and "Use this
  district" re-activates · the nothing-renamed panel names EXACTLY S3's files with the same
  present/absent marking — equality on the name SET + marking, deliberately NOT byte-identical
  ORDER, because S3's is `list(set)` and a byte-identical assertion would flake on `PYTHONHASHSEED`
  rather than bind · each cheap refusal renders its note and attempts NO write (twin: a valid name
  does write) · a save leaves `config.json` byte-identical (twin: activation still writes it) · the
  name-derived copy sweep covers the twelve new constants and the rendered step carries no banned
  vocabulary and no `FORBIDDEN_PROMISES` word.
- **Regression:** full suite · SD74 golden byte-identical · render smokes · `make validate-config`
  20/20 · S3's host-seam test extended to the five callbacks.

#### Acceptance criteria

1. "Your files" shows one row per source file the district's config reads — standard name, name in
   force, what it is used for, whether it is in the input folder — in the same order every run.
2. Setting `StudentSchedule.txt` to `studentcourseselection.txt` and saving produces an overlay in
   which Classes' and Enrollments' `student_schedule` AND
   `global_config.school_year_sources.student_schedule` all name it, every untouched role stays
   inherited, and the file loads through the real `load_config`.
3. A saved change on an already-active district re-closes the FILES step and reopens it after a
   fresh test conversion + confirm; a district with nothing unsaved is unaffected (twin).
4. `OverlaySpec` refuses a rename chain and a case-folded target collision, `write_overlay` refuses
   both before any bytes reach the profile, and SD74's real map still writes.
5. Every typed name passes `authoring.validate_source_filename`; each refusal answers with its own
   bounded sentence, echoes nothing typed, and attempts no write.
6. Exactly one filled primary renders in each of the eight `files_primary_action` states, and the
   test-run button is never the primary while a change is unsaved.
7. S4 performs no `AppConfig` write on any path (pinned by a byte-identical `config.json`), and no
   new copy contains "column", "edit" or "rename" — `FORBIDDEN_PROMISES` unchanged.
8. All gates green (ruff/format, mypy, bandit, tree-check, email scan, 20-config pin), SD74 golden
   byte-identical, `districtsync-design` pass clean, ARCHITECTURE_TREE unchanged (no new module) —
   and **CI's own result read and quoted** before the slice is called landed.

#### Open questions for the owner

- **Two rows pointing at ONE file.** `_build_renames` refuses it; S4 surfaces that as
  `FILES_NAME_DUPLICATE_NOTE` rather than as the bounded write-failure copy. Default: KEEP the
  refusal — two roles reading one file is a data question (which columns win?) the ETL has no answer
  for, and a district genuinely shipping one combined file needs a mapping change, not a filename.
  Should the note point at `help.SUPPORT_EMAIL`? One line either way.
- **Offering names the folder does NOT contain** (the typed field). Default: YES — the folder can be
  empty at setup time, the extract may arrive nightly, and refusing free text would strand exactly
  the district S4 exists for; the honest consequence is on screen already (the chip reads absent and
  the missing-file list names it — `FILES_MISSING_NOTE`'s "carries on without it" story).
- **Should a saved change also explicitly clear `creator_verified[sis]`?** Default: NO — the hash
  comparison already answers, and a refused clear could never make the gate LOOK passed. Named
  because it is the one place a reader may expect an explicit invalidation.

_Spec self-check:_
- **Riskiest element: the post-activation save** — the one path that takes a working district and
  re-closes its gate. Right (the file that converts changed) and safe with no settings write, but
  the only S4 state whose recovery is three clicks; if the owner reads that as friction the answer
  is copy, not a narrower digest.
- **The chain refusal is REACHABLE from the form, so it needs copy, not just a raise.** I nearly
  wrote it as unreachable-by-construction; the dropdown offers folder files, and a folder may hold
  another slot's standard name.
- **One deviation from the brief** (`present_files` → `file_form_rows`) and one declined API
  (`Dropdown(editable=True)`), each with its reason in-line so a later slice does not re-derive it.
- **Vacuous-green watch:** "no `config.json` write", "no overlay written", "the gate re-closed" and
  "the sweep found nothing" are absence assertions, each with its twin — and the equivalence-with-S3
  row is deliberately weakened from byte-identical to set+marking, because a strict-looking
  assertion that flakes on `PYTHONHASHSEED` is worse than an honest one.
- **Not promised here:** per-ROLE divergence (ROADMAP item 1, the model's honest limit) and the
  missing-column report (S5). No S4 string implies either.

#### S4 review ledger (Stage 7, 2026-09-02 — carried into the S4 land record)

Two BLOCKING, both reproduced on the real mount and FIXED in the follow-up commit: (1) the TYPED-name path never re-tiered — typing a name (the only route when the extract has not landed yet) left "Run a test conversion" as the filled primary with no unsaved note, and pressing it tested the config ON DISK, so "Use this district" could activate a district whose typed name was silently dropped (fix: the gate and the confirm REFUSE while `_unsaved()`, plus `on_blur` re-render; a hiding test that typed-then-picked gained a type-only twin); (2) in the WIZARD host, an active district with a pending name edit showed TWO filled primaries — the body's Save and the footer's Continue — and Continue discarded the names (fix: the pending map crosses the existing seam as `build_creator(..., pending=)`, the host renders Continue disabled + secondary while it diverges from the saved form; the `_filled(root)[0] ==` assertion that hid it became list equality). SHOULD, fixed alongside: a homeroom-scoped district lost its `StudentSchedule.txt` row because `advisory_expected_files` drops the schedule while `extract_required_files` still loads it for the school year (`wanted` now unions `school_year_sources`); a hand-edited DIVERGENT overlay resumed as "saved" with no repair prompt (resume now seeds DIRTY on divergence); three spellings of "the same file" (`_folded_name` / `.lower()` / bare `.casefold()`) collapsed onto one public `authoring.folded_filename`; a REFUSED typed name was painted as the name in force (chip/dropdown now keep the last valid name). **Clean on the reviewer's probes:** every typed name crosses `validate_source_filename` before any write (traversal, ADS, reserved devices, control chars, >255 — one sweep over both entry points); all four cheap refusals leave the overlay byte-identical; notes name the check, never the value; `_folder_filenames` returns `()` on an unreadable/missing dir and excludes subdirectories; the write moves exactly `Classes`/`Enrollments`/`school_year_sources` and nothing else; rename-back restores the minimal overlay; the `FLET_1.0_CONVENTIONS.md` line verified against `dataclasses.fields(ft.Dropdown)`. The back-translation of `expected` to base names (implementer flag (a)) is load-bearing — without it a renamed row VANISHES and a saved rename could never be reverted — and is worth a direct test (covered transitively today).

### Slices 5–7
_Spec'd just-in-time, each after the prior slice lands (S5 next — the pure `src/etl/preflight.py` column check, the plain-language "not present in any file" report, and the additive `PipelineResult.input_columns` field wired into the gate beside the dry run)._

---

## Land record — S1 (2026-09-02)

- **Commits (branch `claude/plan-0044-implementation-nrdj2z`):** `is_valid_district_domain` predicate · BC domain table (60 districts / 63 domains, both pinned) · `resolve_config_path` + `validate_overlay` + user-dir domains floor · `authoring.py` (OverlaySpec / build_overlay / write_overlay / delete_overlay) · harness docs (INVARIANTS floor-direction entry, ROADMAP (b) closed, DECISIONS) · reviewer fixes.
- **Deterministic gates (local, Linux):** full suite 4,676 passed / 47 skipped / 1 PRE-EXISTING failure (`test_ui_flet_filepicker.py::TestCheckWritable::test_unwritable_dir_is_rejected` — the container runs as root; fails on a clean baseline too) · `ruff check` + `ruff format --check` clean · `python -m mypy src/ --exclude src/ui_flet` clean · bandit clean · `check_no_emails` OK · `make validate-config` 20/20 · SD74 golden byte-identical · `authoring.py` 100% covered.
- **Acceptance:** (1) `write_overlay(SD93)` → `load_config("sd93custom")` → `python -m src.main --sis sd93custom --input tests/snapshots/input --dry-run` exit 0 (renames reproducing SD74's filenames; also pinned in-process with a no-renames twin); (2) all-defaults overlay `to_raw_dict`-equal to its base for all four `ALLOWED_BASES`; (3) user-dir bad domain row → WARN-and-drop, bundled → raise; (4) no existing test flipped.
- **Reviewer sign-off (Stage 7, adversarial architect pass):** 1 BLOCKING fixed (`_require_bare_filename` accepted Windows drive-relative / ADS `:` names, embedded control chars and reserved device names) · 2 SHOULD fixed (`sis` emission — see the flagged deviation; the shipped-domain parity test enumerated the user dir) · 11 NOTEs: 3 promoted to ROADMAP (per-role divergence, rename chains, SD58/SD70 table quality), the rest accepted as documented.
- **CI:** NOT read — `ci.yml` triggers only on push-to-main / PRs to main. Owed at PR time (flag above).
- **Housekeeping landed alongside:** `.githooks/pre-commit` executable bit (the tree gate had never run on Linux/macOS) · a web `SessionStart` hook installing the toolchain.

## Land record — S2 + S3 (2026-09-02)

- **S2 commits:** `ConfigSummary.origin` + `CUSTOM_ORIGIN_LABEL` ("Added on this computer") in every picker and on Mapping's card (`origin_badge`); invalidation rule at both ends of the config↔UI seam; pins for identity resolve / not-listed-card retirement / friendly name / the #12 non-creator pick path.
- **S3 commits (α β γ δ + two fix rounds):** the `creator_` advisory family behind one `_guarded_field_write` (identity messages byte-identical), `activate_creator_config` (ONE save), `is_config_digest`; `resolved_digest` over the whole validated model + `authored_with` provenance stamped by `write_overlay`; pure `config_editor` (grade chain valid by construction); `setup_flow` creator mode (six steps, four safe-default inputs, `can_finish` gated on activation); `screens/creator.py` (`build_creator` host seam, 42 copy constants swept) + `creator_gate_job` (refuses an unusable output dir before importing the pipeline).
- **Deterministic gates (local, Linux, after the last fix):** full suite 5,149 passed / 47 skipped / 1 PRE-EXISTING root-container failure · ruff + format clean · `python -m mypy` clean (non-UI + `config_editor`) · bandit clean · email scan OK · `make validate-config` 20/20 · tree-check OK · SD74 golden byte-identical.
- **Reviewer sign-offs (Stage 7):** foundations pass (S2 + α/β/γ) — no BLOCKING in reviewed code, 3 SHOULD fixed; δ pass — 2 BLOCKING fixed (grades seed; superseded overlay), 5 SHOULD fixed (Finish asserted end-to-end; `creator.py` module; field rules into `config_editor`; `check_row`; one spelling + lazy catalog), NOTEs ledgered above. Security/PII probes clean on both passes.
- **Spec deviations, all in DECISIONS:** overlays inherit `sis`; `build_creator` gained `stage` + `on_written(note)`; the creator lives in `screens/creator.py` from S3, not S6. `sis_type` write sites are FOUR (the gated one added).
- **CI:** still not read — owed at PR time (owner: PR after the UI slices).
- **Local test recipe for the owner:** `DISTRICTSYNC_DATA_DIR=<scratch abs path> python -m src.main` → launch page → (any address, or the not-listed escape) → wizard District step → "Set up my district" → four forms → Continue → Folders (point input at a GDE folder, e.g. a copy of `tests/snapshots/input/` — note SD74's filenames differ from the standard, so with S3 alone the test conversion reports them missing; S4 adds the rename form) → "Your files" → "Run a test conversion" → "Use this district" → Delivery/Schedule → Finish.

## Land record — S4 (2026-09-02)

- **Commits:** the filename form (`distinct_source_files` · `file_form_rows` · `CreatorForm.renames`/`with_rename` · `renames_from_resolved` · `files_primary_action` · public `validate_source_filename` + the folded chain/duplicate refusals · the "Your files" rows + ONE "Save these file names" above the gate · fifth callback `on_files_saved`) + the review-fix commit (gate/confirm refuse while unsaved · `on_blur` re-tier · host-owned `pending` map disabling the footer Continue · `school_year_sources` slot union · divergence-aware resume · one `folded_filename` · refused names never shown as in force).
- **Deterministic gates (local, Linux, after the fixes):** full suite 5,312 passed / 47 skipped / 1 PRE-EXISTING root-container failure · ruff + format clean · `python -m mypy` clean · bandit clean · email scan OK · `make validate-config` 20/20 · tree-check OK (100 files) · SD74 golden byte-identical.
- **Reviewer sign-off (Stage 7):** 2 BLOCKING fixed · 3 SHOULD fixed · 1 NOTE fixed · 2 tier-gap residuals to ROADMAP. Security/PII, write-correctness and docs clean on the reviewer's probes.
- **Closes:** ROADMAP "S1 review item 2" (rename chains). **Records:** DECISIONS (pending map host-owned; gate refuses while unsaved). `FLET_1.0_CONVENTIONS.md`: `Dropdown(editable=True)` declined + `TextField.on_blur` verified.
- **CI:** still not read — owed at PR time (owner: PR after the UI slices).
- **Milestone:** S1–S4 = the locally-testable self-service UI (standard AND renamed filenames). Remaining: S5 (missing-column report), S6 (Mapping edit + re-gate on Apply/folders-card Save), S7 (export + docs + certification disposition).
