# 0053 — ETL failure policy: entity bulkhead, declared criticality, typed outcomes, and one standard future agents cannot miss

- **Status:** In Review → Spec'd pending Gate A. Orchestrator-synthesised from a 112-agent research/design/refutation workflow (2026-09-23); Stage 3 plan-review (Opus, acting as `plan-reviewer`) returned **CHANGES REQUIRED × 16 + 6 recommended** the same day. Re-review: the 16 required and recommended #17/#21 applied; the re-review's residuals (#4/#7 partials, stale text, #12/#16 partials, `reason_for` home, `PipelineResult` default, #18–#20/#22) applied in the second pass, marked *(R#)*; the final check's four one-line edits (S5 wording, decomposition S4 line, CLAUDE.md :176/:236, `kw_only` + constructor sites) applied in the third — the reviewer stated a diff check suffices after these, so Stage 3 is complete. See `## Review`. **Gate A decided 2026-09-23 (owner): D1 = (c) all four optional feeds ISOLATABLE (Family, StudentAttendance, CourseInfo, StudentCourses); D3 = (a) exit 0 + PARTIAL; D4 = (a) validated config-declared labels.** D2 (send Q5) is the owner's action. Remaining gate item: owner approval of §3/§5 once S0 writes them. D5–D14 open.
- **Resumable from:** S0 (in progress on `claude/0053-s0-failure-policy`) → owner approval of §3/§5 → S1. Handover: `.claude/plans/0053-HANDOVER.md`.
- **Blockers:** none for S0–S13. S14 waits on SpacesEDU (Q5). S7/S8/S15 wait on owner decisions.
- **Roadmap items closed or cross-referenced:** `docs/claugentic-ROADMAP.md` — the T1 homeroom-rename / dead `context.py` path item; the all-Active default item; the partial-ship item (`enrollments.py:159-165`, ROADMAP:54); the crashed-Convert-unrecorded item; the divergent-resolvers item; the SD67 silent-blank item (:155); the `transfrom:` item (:43); the `ALL_BUNDLED_CONFIGS` hand-list item (:93); the `history.db` migration-ladder hazard (:50 — NOT fixed here; it is why this plan adds no DDL).
- **References:** `docs/claugentic-ARCHITECTURE_TREE.md` · `docs/claugentic-DECISIONS.md` (2026-09-09 staff fail-open; 2026-09-14 row_filters ordering; 2026-09-16 Unity Family on; 2026-09-18 SD51 header echo) · `docs/claugentic-INVARIANTS.md` · `docs/developer/output-contract.md` (graded-table style to copy; Open owner questions at ~:594) · plans 0050 (pre-flight precedent), 0051 (Convert loads the config's file set), 0052 (staff role) · `docs/partner/faq.md:19-21, :107-128`.
- **Baseline commit:** `8d33664` (v3.25.0). Every `file:line` below was verified against it by the research agents unless marked *(verify)*.

---

## Problem

**The incident (Unity Christian, 2026-09-23).** The school's 2026-09-22 drop reverted `EmergencyContactInformation.txt` from the ENHANCED report (23 columns) to the PLAIN report (18 columns) under the same filename. `unitychristianmyedbc` filters Family on `Parent Auth / Guardian`. `BaseTransformer.apply_row_filters` (`src/etl/transformers/base.py:483-488`) raised — correctly: shipping unfiltered rows would deliver doctors, aunts and homestay coordinators as Family PII. But `run_transform`'s entity loop (`src/etl/pipeline.py:320-352`) has skip-and-continue branches only for an EMPTY entity (:325/:330/:341/:348) and **no seam for an entity that RAISES** (:345). So Students, Staff, Classes and Enrollments — all built correctly — were discarded; exit 1; `error_category = "data"`; and the desktop Convert screen showed the zero-argument `convert_error_copy()` (`src/ui_flet/convert_result.py:230-245`): *"check that your input folder holds this district's MyEd BC extract files"* — for a folder that was correct. A run with Family removed from `enabled_entities` produced 667 students / 46 staff / 147 classes / 2,738 enrollments.

**This is not a row_filters bug. It is a missing architectural unit: the unit of failure.** Around that missing seam, the research found the same shape repeated:

| # | Finding | Evidence |
|---|---|---|
| G1 | One entity's raise kills every entity; Convert fails identically (PR #142 loads the same file set) | `pipeline.py:345`; `screens/convert.py:315` |
| G2 | No declared required/optional entity. The only notion is the implicit Students anchor, which judges output SHAPE after the fact, never an exception | `pipeline.py:61` `ROSTER_ANCHOR_ENTITY`; `check_delivery_integrity` :404 runs only after `run_transform` returns |
| G3 | "A needed column is missing" has FIVE postures depending on which helper is called: RAISE (`apply_row_filters`; `students.py:136-140`, `:282-286`; `grades.py:393-403`), raw pandas `KeyError` (`classes.py:384-405`), WARN-fail-open (`staff.py:281-299`; `base.py:332-378` `filter_to_active` — base.py:366-368 returns the frame UNFILTERED), SILENT (`staff.py:274-275`; `_merge_roster` :325-353; `classes.py:131-162`; `enrollments.py:290-293`; `blended._teacher_from_frame`/`_build_teacher_name_map`; `apply_field_map`'s intended blank ~:753), PARTIAL SHIP (`enrollments.py:159-161` catches `(KeyError, MergeError)` and ships what built) | as cited |
| G4 | Three column resolvers with different bare-string semantics; `resolve_column` (`base.py:512-527`) silently ignores a bare-string mapping; `context.py:148-149` still reads the dead `global_config["mappings"]` path; `tests/conftest.py:327` injects a shape production never produces, masking it | as cited |
| G5 | `src/etl/preflight.py` already derives expected columns incl. row_filters (`:61`, `:128`, `:358`, `:431`) but its only runtime caller is the creator (`screens/creator.py:491`); `preflight._observed_names` (:411-430) merges every file's headers, so a column in Staff's file hides a miss in Family's | as cited |
| G8 | Classification is by type + message substring (`pipeline.py:759` `"No usable required input" in str(exc)`), contradicting its own docstring (:744-747); `ExtractionError` (`extractor.py:41`) is never caught by name → UNKNOWN; 12 exception classes with no common base | as cited |
| G9 | No bounded reason reaches the admin; privacy rule `home_status.py:22-26` bans free-text `str(e)` (rightly) but nothing else exists | as cited |
| G10 | A failed manual Convert writes NO run record (`_record_manual_run` :517-525 is called only on non-exception paths; `run_transform` at :315 is uncaught); `run_pipeline` :1091 does record | as cited |
| G11 | An enabled entity that produces nothing is GREEN (SD51 2026-09-21: Family 4,875/4,875 rows excluded, "skipping", run green); the vanished-entity anomaly needs the previous CSV, which the SAME run archives (`compute_anomalies` :532-543 then `archive_stale_outputs` :1010) — so the alarm lasts ONE night *(code-read, not executed — verify in S4's two-night test)* | as cited |
| G12 | `runs.status` CHECK allows only success/failed; `error_category` is unconstrained TEXT; no per-entity outcome anywhere in `PipelineResult`, `ConvertResult`, the record or `history.db` | `store.py:69-80` |
| G14 | Students' active filter degrades silently when the status column is absent (INFO at `base.py:288-294`; only the neither-column case WARNs :295-301) | as cited |
| G15 | `GlobalConfig`/`EntityConfig`/`FieldTransform`/`FieldNameConfig` do not forbid extras: `enabled_entites` or `transfrom:` validates and is silently dropped | `models.py` ~897-915 |
| G16 | No written failure policy; `docs/claugentic-standards/*` are service/RDBMS-shaped managed copies with no ETL module; rationale lives in dated DECISIONS prose | — |
| G18 | `faq.md:19-21` promises a missing file "skips the affected entity … other entities are processed normally" — false for a present file with changed columns; its Course Information example is wrong (Classes reads 5 files, `myedbc_mapping.yaml:171-176`); `help-centre-myedbc-districtsync-guide.md:175` claims the Enhanced report's extra columns are ignored — false (base Family maps `Email Address`) | as cited |

**The one fact nobody in the repo has.** `docs/partner/faq.md:109` — *"Users that no longer appear in `Students.csv` or `Staff.csv` (by User ID, Role, and School ID) are marked Inactive"* — and `:113` (Enrollments → unenrolled) cover rows missing from a DELIVERED file. **Nothing says what SpacesEDU does with an ABSENT CSV**, for any of the 8 files, nor whether a guardian missing from `Family.csv` is unlinked. Every "skip this entity and deliver the rest" decision depends on that answer. Two code facts bound the risk meanwhile: the code never delivers a header-only CSV (an empty transform is skipped, never written, :346-348; the manifest is exactly the files written, `uploader.py:514-519`, `pipeline.py:1026`); and absent-CSV deliveries **already happen** (SD51 shipped without `Family.csv` on 2026-09-21; `tests/test_pipeline_delivery_integrity.py:581-590` pins an empty-contacts night as legitimate, and :592-601 pins Staff/Classes/Enrollments shipping ABSENT with exit 0 — an empty entity is skipped, not written). So isolating a failed optional entity creates **no new delivery shape** — it makes an existing one loud.

## Goals / Non-goals

**Goals**
1. A declared bulkhead at the ONE entity seam, shared by CLI and Convert: an ISOLATABLE entity fails alone, loudly, and heals itself when a correct export returns — no release. Every CRITICAL entity fails the whole run byte-for-byte as today.
2. Typed errors + closed vocabularies, classified by type only; a per-entity outcome on every run record (additive JSON, no DDL).
3. Level-triggered surfacing: a partial run is WARNING on Home, Run History and Convert every run it persists, never green; failure copy names the KIND of problem, never the input folder by default.
4. ONE repo-local standard, `docs/developer/failure-policy.md`, written first, approved by the owner, pinned by parity/totality/AST tests so a deviating agent gets a red test that names the section.
5. Conformance of every column-handling site to one written matrix; one column resolver; config typos loud; symmetric sinks; architecture fitness tests.

**Non-goals (explicit)**
- Making `row_filters` or any PII-scope guard fail open; dropping Unity's guardian filter.
- Any substitution for a failed entity (last-good CSV, partial frame, filter-less mapping).
- Isolating Students/Staff/Classes/Enrollments before Q5 is answered (S14 governs any change to the table after the answer).
- Changing skip-on-empty for CRITICAL entities (D6) before Q5.
- A declarative "report variants" config knob (one school, one file — revisit on a second same-filename shape flip). The typed missing-column failure + own-file observation already recognises "the plain report" without naming it.
- An entity on/off toggle in the UI or `AppConfig`, or a "skip this run" switch (D12). After S4 a broken optional file fails alone and recovers alone; a toggle would turn a loud temporary fault into a silent permanent omission.
- Per-file extraction isolation (`extractor.load_data` stays all-or-nothing; S1 only classifies it). ROADMAP.
- Persisting header fingerprints / diffing file shape between drops (G7). Storing observed header names risks storing a pupil row from a mis-read headerless file (the SD51 header-echo shape). ROADMAP.
- Any `history.db` DDL, CHECK or `user_version` bump; the ROADMAP:50 migration-ladder bug must be fixed before any future DDL.
- Retiring `to_raw_dict()` for a typed `ResolvedEntitySpec`, splitting `BaseTransformer`/`pipeline.py` wholesale, a Result monad, a rules engine, `import-linter`. S1/S2/S9 extract exactly `errors.py`, `outcomes.py`, `columns.py`.
- A new exit code for a partial delivery (D3).
- `map_role`'s permissive default, the `StudentAttendance` roster-cascade gap, widening `row_filters` beyond Family/Staff, sharing role→filename bindings (sd67). Each has its own ROADMAP item.
- Editing managed `docs/claugentic-standards/*`; lessons go to `CANDIDATES.md`.
- Committing any real partner drop.

## Approach

**Spine (orchestrator decision).** Judge 1's full per-entity outcome model (every configured entity gets an outcome every run; `errors.py` + `outcomes.py`), with these reconciliations from the adversarial round: `compute_anomalies` stays UNCHANGED (its vanished-entity leg is what drives Convert's consent gate — `convert.py:344-351` — so a failed Family with a previous `Family.csv` still asks before archiving; PARTIAL simply outranks ANOMALY in verdict precedence); the own-file observation (S6) is advisory and NEVER edits configs; `RunErrorCategory` becomes a `StrEnum`; the error `category` is a plain instance attribute with a class-level default (no ClassVar-override trick — mypy rejects it); observed header lists appear in NO exception text and NO log line (PII floor); Convert's failure sink ends before the SFTP leg; `deliver_job` is UNCHANGED (a filter by the newest record's BUILT set would ship `Students.csv` alone after a critical failure — the archive-on-the-same-run rule is the guarantee, and Home's PARTIAL walks back to the build record).

**Reader before producer.** S3 (UI reads PARTIAL) lands before S4 (the bulkhead produces it), following the plan-0049 inert-slice precedent, so no build can ship a partial run that shows green.

**Criticality is declared, restrictive by default, evidence-promoted.** `outcomes.ENTITY_CRITICALITY` covers every registry entity; `criticality_of()` returns CRITICAL for anything unlisted (a hand-dropped YAML entity included). Promotion to ISOLATABLE requires (a) a recorded partner answer OR the "absence already ships" evidence, (b) publishes no `TransformContext` state, (c) no CRITICAL entity reads its output, (d) dependency withholding declared, (e) a dated DECISIONS entry.

**The asymmetric-risk rule (P1, restated after refutation).** H1 — never widen delivered PII: a scoping rule that cannot be applied fails CLOSED at the smallest scope containing the fault. H2 — never silently shrink or omit a deactivating file **because of a detected fault**: the run fails, the shrunken file never ships. (Deliberate, configured or heuristic exclusions — departed staff, plan 0052's un-roled staff, withdrawn students — are H3 and stay under the existing anomaly/integrity gates; this rule does not reverse them.) H3 — surplus/harmless rows: fail OPEN with a recorded signal. H4 — an optional value blank: recorded.

**Alternatives rejected (one line each):** make `row_filters` fail open (ships non-guardian PII) · "skip for this run" toggle (silent permanent omission) · last-good CSV fallback (stale PII) · declarative schema variants (YAGNI, one district) · new exit code 4 (breaks the documented contract for no consumer) · Result monad / typed-spec migration (disproportionate for a batch exe) · per-file extraction isolation (same promotion rule applies; ROADMAP) · keeping CourseInfo/StudentCourses CRITICAL (the orchestrator's recommendation; the owner chose to isolate all four optional feeds under D1 — see D1).

### Policies P1–P16 (what `failure-policy.md` §1–§11 state; each becomes one INVARIANTS row) *(R2)*

| P | Rule (one line) | Enforced by |
|---|---|---|
| P1 | Asymmetric risk: H1 never widen delivered PII (fail CLOSED at the smallest scope); H2 never silently shrink/omit a deactivating file **because of a detected fault** (run fails); H3 surplus rows fail OPEN with a recorded signal; H4 optional blanks recorded | §5 matrix; site tags ↔ catalogue bijection (S11) |
| P2 | Bulkhead: transformers RAISE typed errors and never catch to continue at entity scope; exactly ONE entity-scope boundary exists, in `run_transform`, shared by both entry points; CRITICAL → bare `raise` of the original object; ISOLATABLE → recorded, logged once at ERROR with traceback, `continue`; `BaseException` never caught | isolation tests + AST one-boundary pin (S4) |
| P3 | Criticality declared once in `ENTITY_CRITICALITY`; unknown ⇒ CRITICAL; promotion needs (a) partner answer or absence-already-ships evidence, (b) publishes no context state, (c) no CRITICAL reader, (d) dependency declared, (e) DECISIONS entry | totality + context-publisher AST pin (S2); §3 parity |
| P4 | Never substitute a FAILED/EMPTY entity (no last-good CSV, no unfiltered rows, no partial frame, no filter-less mapping); its previous CSV is archived out of the glob and its absence reported every run | S4 contract fixture (marker + manifest) |
| P5 | Missing-column matrix keyed on what the column GUARDS: (a) PII_SCOPE fail CLOSED; (b) JOIN_KEY fail CLOSED, typed, never a raw KeyError or partial result; (c) CONTRACT_FIELD: rows lacking it excluded, COUNTED, all-excluded ⇒ EMPTY with reason; (d) OPTIONAL_FIELD: blank + one aggregated WARNING + note; (e) SAFETY_HEURISTIC: fail OPEN only in the surplus direction, always recorded | `require_columns` (S10) / `record_outcome_note` (S11); catalogue bijection |
| P6 | Typed errors: every boundary-crossing failure is an `EtlError` carrying a bounded category; classification by `isinstance` only, never message text; every closed-enum member maps to copy, verdict and doc row | `test_etl_errors.py` completeness + AST no-`str(` pin (S1); copy totality (S3) |
| P7 | Level-triggered: every configured entity has exactly one outcome per run; any FAILED outcome in a successful run ⇒ PARTIAL/WARNING every run it persists, derived from the run's own record, never from a baseline the run archives | two-night test (S4); `ledger.complete()` (S2) |
| P8 | Symmetric sinks: every build attempt on every entry point writes exactly one record through `build_run_record`; `entity_outcomes`, `ledger`, `status`, `error_category` are required keyword-only; a convenience default is allowed only where it errs toward MORE warnings | signature tests (S2/S5); `test_convert_failure_record.py` |
| P9 | Bounded surfacing: records/banners/cards carry closed-set codes humanised through total copy tables; never `str(e)`, cell values, paths or OBSERVED header text; config-declared labels only under D4, membership-validated + sanitised | sentinel tests (S1/S3/S7) |
| P10 | One column resolver (`columns.resolve_source_column`) with one shape policy built on `models.classify_field`; presence checks via `columns.require_columns`; new shared behaviour goes in composed modules, not `BaseTransformer` methods | AST pin (S9) |
| P11 | Source observation never enforces: it attributes missing mapped columns per entity over that entity's OWN files, never raises, never gates, keeps preflight's soundness rule | raise-isolation twin (S6) |
| P12 | Additive record evolution: new facts are new JSON keys; no `runs` DDL/CHECK/`user_version` change until ROADMAP:50 is fixed; readers are TOTAL; an unknown kind/reason from a newer build reads as FAILED/TRANSFORM_ERROR (errs toward a warning) | `outcomes_from_record` totality (S2) |
| P13 | Partner assumption: any policy depending on SpacesEDU import behaviour cites a dated confirmation row in `output-contract.md` or the open question (Q5); until confirmed the conservative branch applies; partner docs never claim "no side effects" for an omitted file while Q5 is open | FAQ ↔ `Q5-status` parity (S4) |
| P14 | Docs follow code and are pinned: every rule row carries ENFORCED / PLANNED (0053 Sn); a slice flips its rows in the same change; every table mirroring a code constant has a parity test with a non-vacuity assertion and a doctored-doc twin | `test_failure_policy_parity.py` (S2 →) |
| P15 | Config typos are loud, origin-keyed: bundled RAISE at load; user-dir WARN at run / REFUSE at authoring; `Field*` leaf models forbid extras everywhere; root stays `extra="ignore"` | `test_config_unknown_keys.py` (S12) |
| P16 | Layering: `src/etl`, `src/config`, `src/history`, `src/quality` never import flet/`src.ui_flet`; an explicit `FLET_FREE_MODULES` list never imports flet; transformers never import `pipeline`; every in-scope `except Exception` carries a reasoned `noqa: BLE001` | `test_architecture_fitness.py` + ruff BLE (S13a) |

### Naming table (single source — every slice uses exactly these)

| Thing | Name | Home |
|---|---|---|
| Category enum (moved, `StrEnum`) | `RunErrorCategory` + new `SOURCE_SCHEMA="source_schema"`, `INPUT_UNREADABLE="input_unreadable"` | `src/etl/errors.py` |
| Error base / leaves | `EtlError`, `SourceSchemaError(EtlError, ValueError)`, `NoUsableInputError(EtlError, RuntimeError)`; `ExtractionError`, `DeliveryIntegrityError`, `OutputWriteError` re-parented in place | `src/etl/errors.py` (+ in-place rebases) |
| Guard classes | `GuardKind(StrEnum)`: `PII_SCOPE`, `JOIN_KEY` | `src/etl/errors.py` |
| Classifier | `classify_error_category(exc) -> RunErrorCategory` (public) | `src/etl/errors.py` |
| Reason mapper | `reason_for(exc) -> OutcomeReason` (`SourceSchemaError` → `MISSING_SOURCE_COLUMN`, else `TRANSFORM_ERROR`) — lives beside `OutcomeReason` because `outcomes` imports `errors`, never the reverse | `src/etl/outcomes.py` |
| Criticality | `EntityCriticality(StrEnum)`: `CRITICAL`, `ISOLATABLE`; `ENTITY_CRITICALITY`; `criticality_of(name)`; `ROSTER_ANCHOR_ENTITY` (moved) | `src/etl/outcomes.py` |
| Outcomes | `OutcomeKind`: `BUILT, EMPTY, FAILED, NOT_RUN`; `OutcomeReason`: `NONE, NO_SOURCE_FILES_DECLARED, SOURCE_FILES_EMPTY, NO_ROWS_AFTER_TRANSFORM, MISSING_SOURCE_COLUMN, TRANSFORM_ERROR, RUN_ABORTED`; `VALID_REASONS`; `EntityOutcome(entity, kind, reason, rows, missing_mapped=(), notes=())` frozen, illegal states refused in `__post_init__`; `OutcomeLedger`; `OUTCOMES_RECORD_KEY = "entity_outcomes"`; `outcomes_to_record`, `outcomes_from_record` (TOTAL), `failed_entities` | `src/etl/outcomes.py` |
| Column helpers | `resolve_source_column(field_map, key, *, default)`; `require_columns(available, required, *, entity, guard)` | `src/etl/transformers/columns.py` (pandas-free) |
| Copy | `entity_phrase`, `outcome_sentence`, `FAILED_CATEGORY_COPY`, `error_card_copy(exc)`, `OUTCOME_TIER` | `src/ui_flet/failure_copy.py` (COUNTED, no flet) |
| Verdict rung | `LatestReason.PARTIAL = "partial"` → `Verdict.WARNING` | `src/ui_flet/home_status.py` |
| Standard | `docs/developer/failure-policy.md` §0–§14, Status column `ENFORCED` / `PLANNED (0053 Sn)` | docs |
| Tests | `tests/test_etl_errors.py`, `tests/test_etl_outcomes.py`, `tests/test_failure_policy_parity.py`, `tests/test_pipeline_entity_isolation.py`, `tests/test_ui_flet_failure_copy.py`, `tests/test_convert_failure_record.py`, `tests/test_column_resolution.py`, `tests/test_require_columns.py`, `tests/test_config_unknown_keys.py`, `tests/test_architecture_fitness.py` | tests |
| Site tags | `# failure-policy: pii_scope|join_key|contract_field|optional_field|safety_heuristic` | every §5 site |

### The layer stack after this plan (each layer owns one question)

config parse (Pydantic; typos loud — S12) → extract (file state; typed `ExtractionError` — S1) → **source observation** (advisory, own-file, never enforces — S6) → **per-entity isolated transform** (bulkhead + outcome — S2/S4) → delivery-integrity gate (unchanged floor) → atomic write → deliver (manifest = files this run wrote — unchanged) → run record carrying `entity_outcomes` (S2/S5) → pure copy mappers (S3).

The two layers in bold are the ones the incident showed missing.

### Architecture & holistic fit *(R16)*

- **Where the new modules sit:** `src/etl/errors.py` and `src/etl/outcomes.py` are stdlib-only leaves under `src/etl` (importable by transformers, pipeline and the UI without cycles); `src/etl/transformers/columns.py` is a pandas-free composed module beside `grades.py`/`sources.py` (the existing pattern for extracting from `BaseTransformer`); `src/ui_flet/failure_copy.py` joins the COUNTED pure UI modules and imports label maps from `humanize.py`, never from `home_status.py` (which imports it — R8).
- **Dependency direction after the plan:** `errors ← outcomes ← {transformers, pipeline} ← {ui_flet.job_runner, screens}`; `failure_copy ← {home_status, run_history, convert_result}`. No module under `src/etl` imports `src.ui_flet` (pinned S13a). `RunErrorCategory` moving out of `pipeline.py` is what lets transformers raise typed errors without importing the orchestrator.
- **Boundaries preserved:** `check_delivery_integrity` stays the post-transform floor; `save_all`/`archive_stale_outputs`/the upload manifest are untouched — the bulkhead only changes what reaches `outputs`. `compute_anomalies` is untouched so Convert's consent gate keeps its single trigger. The self-service creator's activation gate is the one place the bulkhead would otherwise have WIDENED behaviour, and S4 closes it.
- **Record contract:** one additive JSON key (`entity_outcomes`) beside `run_as`, written by the one `build_run_record`, read by the one `outcomes_from_record`; Home/Run History/Convert classify through the one `classify_latest_reason`. Older exes ignore the key.
- **Pattern lineage:** the typed-error + canonical-message discipline copies `src/scheduler/task_com.py`/`windows._fail`; totality tests copy `_MACHINE_SCOPE_CAUSES`; the inert reader-before-producer ordering copies plan 0049; the origin-keyed config severity copies `_apply_user_dir_domains_floor`; the measurement-gate-then-STOP rule copies plan 0052.
- **Quality dimensions → standards modules (`docs/claugentic-standards/`)** *(R16)*: privacy & data governance (TOP) → P1/P4/P9, sentinel tests, no observed headers anywhere; reliability-resilience → P2 bulkhead, P7 level-triggered, P11 observation-never-enforces; data-and-persistence → P12 additive record evolution, no DDL; observability-ops → `ENTITY NOT BUILT` grep anchor, `entity_outcomes` in the `__DISTRICTSYNC_RUN__` line and the store, exit-code contract unchanged; security → no new subprocess/keyring/host surface, `ALLOWED_TRANSFORMS` untouched, bandit after the last edit; maintainability-structure → three composed leaf modules instead of `BaseTransformer` growth, P16 fitness tests; testing → totality/parity/AST/schema-drift families with positive+negative twins; api-and-contracts → `output-contract.md` MINOR bump for the "Family.csv may be absent" envelope row, config `version:` untouched; product-ux → verdict-first WARNING band, one filled primary, `districtsync-design` skill loaded for S3/S7/S8.
- **Product fit:** the admin's question is still "did the roster sync?" — PARTIAL answers "yes, minus one named file, and here is what to re-export"; nothing new to configure; recovery is automatic on the next correct drop; the vendor gains a bounded category in Run History for support.
- **Future-proofing:** a new entity cannot be added without declaring criticality (totality test) or classifying its columns (site-catalogue bijection); a new failure reason cannot ship without copy (totality); promotion to ISOLATABLE is one table line + parity rows; the record key is additive so a future DDL migration (ROADMAP:50) is unaffected; the declarative "report variants" knob, per-file extraction isolation and header fingerprinting are named ROADMAP items with stated triggers, not half-built here.

### Answers to the owner's questions (recorded here so they survive the plan)

- *Per-entity isolation gap?* Yes — S1–S4.
- *Can the UI know it's the plain report?* Yes, without hardcoding report names: a typed `SourceSchemaError(entity=Family, columns=("Parent Auth / Guardian",), guard=PII_SCOPE)` + the own-file observation say exactly which file lacks which configured column. Shown on screen only if D4 approves config-declared labels (S7). Heals itself when the Enhanced report returns.
- *Toggle?* No (D12). Two routes already exist (vendor config change; self-service creator overlay), and both are validated + visible.
- *Are the validation/error-handling layers appropriate and obvious?* Not today: two layers are missing, five postures coexist, and the rules are prose scattered across dated DECISIONS entries. After S0+S13: one doc, one naming table, red tests that name the section.
- *Principles beyond SOLID/DRY/KISS/SRP that this codebase needs and did not name:* **bulkhead / blast-radius containment** (the root cause); **asymmetric-risk safe-failure direction** (the tie-breaker every posture derives from); **schema-on-read data contracts** at the ingestion boundary (preflight exists but isn't in the run path); **level-triggered, not edge-triggered, alarms** (the one-night decay); **observability of absence** (no silent success); **symmetric sinks across entry points**; **typed error taxonomy with closed reason codes** (the scheduler already models it — `task_com.MSG_*`, `_fail`); **totality/exhaustiveness tests over closed enums** (already used for `_MACHINE_SCOPE_CAUSES`); **architecture fitness functions** (layering enforced by a test, not by review); **ADR practice with a standing-policy index**; **parse-don't-validate at the config boundary** (deferred — `to_raw_dict()` stays; ROADMAP); **functional core / imperative shell** (already the UI's pattern; applied to the outcome layer). Already embodied and kept: OCP via registry+config, YAGNI, no-permissive-default, privacy-by-design, idempotent atomic writes, additive schema evolution, 12-factor-ish config.

## Affected files

Pattern once; representative paths per slice are in **Decomposition**. New source files (each needs an `ARCHITECTURE_TREE` line): `src/etl/errors.py`, `src/etl/outcomes.py`, `src/etl/transformers/columns.py`, `src/ui_flet/failure_copy.py`. Heavily edited: `src/etl/pipeline.py`, `src/etl/transformers/base.py`, `src/ui_flet/screens/convert.py`, `src/ui_flet/home_status.py`, `src/ui_flet/convert_result.py`, `src/etl/preflight.py`, `src/config/models.py`/`loader.py`/`authoring.py`. Docs: `docs/developer/failure-policy.md` (NEW), `output-contract.md`, `docs/partner/faq.md` + `help-centre-myedbc-districtsync-guide.md` + `troubleshooting.md`, `INVARIANTS`, `DECISIONS`, `ROADMAP`, `CANDIDATES`, `CLAUDE.md` (S0: +1 line; S4: two in-place sentence edits; S15: restructure, owner-gated).

**Conflict map (serialise within a row; rows are parallel-safe):**

| File | Slices that edit it | Order |
|---|---|---|
| `src/etl/transformers/base.py` | S1 (row_filters raise), S9 (resolver), S10 (require_columns), S11 (notes) | S1 → S9 → S10 → S11 |
| `src/etl/pipeline.py` | S1, S2, S4, S6 | S1 → S2 → S4 → S6 |
| `src/ui_flet/screens/convert.py` | S2, S3, S4, S5, S6 | S2 → S3 → S4 → S5 → S6 |
| `src/etl/outcomes.py` | S2, S6, S7, S11 | S2 → S6 → (S7 ∥ S11) |
| `src/config/*` | S12 only | parallel with S9–S11 |
| `src/ui_flet/home_status.py` + `humanize.py` | S3 (labels move, PARTIAL), S7 (labels), S8 (tier) | S3 → S7 → S8 |
| `tests/conftest.py` | S2 (:760 `build_run_record` call), S9 (delete the `mappings` injection :327), S13a (count constant) | S2 → S9 → S13a |
| `docs/partner/faq.md` | S0 (truth today), S4 (criticality rule), S8 (empty-entity warning), S14 (Q5 answer) | in slice order |
| `docs/developer/failure-policy.md` | every slice flips Status rows | merge-conflict-prone: each slice touches only its own rows |

## Risks & mitigations

- **Guardians already linked at Unity (since 2026-09-14) may be unlinked by a delivery without `Family.csv`.** Unknown (Q5). Same delivery shape as an existing empty-contacts night; recoverable on the next good night; the owner accepts it explicitly in D1. The alternative keeps a CERTAIN harm (the whole roster frozen).
- **Isolating an entity that a critical one depends on.** Prevented structurally: Classes→Enrollments (`context.class_artifacts`) and everything→Students (`active_student_ids`) are the only context publishers (`students.py:47`, `classes.py:49` — AST-pinned in S2); both are CRITICAL.
- **A bug in Family's own code now shows as a nightly WARNING rather than a failure.** Still loud: ERROR + traceback in the log, PARTIAL every night on every surface, `ENTITY NOT BUILT` grep anchor.
- **Older exes sharing `history.db` paint a partial run green** (two builds on one machine, `store.py:21/200`). Accepted: the partial record keeps `status="success"` (exit-0 contract; older readers degrade to today's behaviour, never to a crash). Documented in S2.
- **SD74 snapshot / contract goldens.** Every code slice states "byte-identical" as an AC; S9/S10 carry a measurement gate and STOP-and-escalate rules (D8/D9). Never update goldens silently.
- **Signature churn** (`run_transform(*, ledger)`, `build_run_record(*, entity_outcomes)`, `_record_manual_run(*, status, error_category)`): grep every test caller before landing (`run_transform(` ≈11 sites, `build_run_record(` ≈3 sites in tests/ *(verify)*).
- **Doc drift before pins exist.** The Status column + S2's parity test; the doc lands in S0 with every row truthful about TODAY.
- **PII in exception text.** `base.py:486-487`'s `Available: {sorted(df.columns)}` (and `grades.py:396`, `students.py:139/285`) can carry a pupil row when a headerless export arrives without its header line. S1 removes observed header lists from exception AND log text: missing CONFIG names + available-column COUNT only.
- **Convert's consent gate.** Keep `compute_anomalies` unchanged; a failed entity with a previous CSV still prompts (the prompt gains the not-built reason).
- **`deliver_job` ships from disk** (`convert.py:451-508`) and stays that way. The failed entity's previous CSV was archived out of the folder on the same run, so a resend cannot carry it; a delivery record carries `entity_outcomes=None` and Home's PARTIAL walks back to the build record (S3/S5), so a resend cannot paint a partial build green. (Filtering by a record's BUILT set was considered and rejected: after a critical failure it would ship `Students.csv` alone.)
- **Release timing.** Cut a release after S4 (Unity's fix) — S5+ are hardening.

## Test strategy

Every slice: positive + negative twin per "no vacuous greens"; the SD74 snapshot; the 20 contract configs (`tests/test_contract.py` builders); `tests/test_pipeline_parity.py` (CLI ≡ Convert); coverage ≥80% with new pure modules counted. Plan-wide new families: (1) **isolation** — parametrised over `ENTITY_CRITICALITY`: every ISOLATABLE raise leaves the rest written; every CRITICAL raise re-raises the SAME object (`is`) with the output dir untouched; two consecutive nights stay PARTIAL; (2) **Unity plain-report contract fixture** (18-column `EmergencyContactInformation.txt`, planted non-guardian marker name) with the Enhanced builder as its twin; (3) **totality** over every closed enum → copy, category, doc row; (4) **doc↔code parity** (`tests/test_failure_policy_parity.py`: §3 table = `ENTITY_CRITICALITY`; §6 = enum members; §5 site catalogue ↔ `# failure-policy:` tags bijection; FAQ isolatable sentence = ISOLATABLE labels + pending clause while `Q5` open) each with non-vacuity + doctored-doc negative twin; (5) **AST pins** (one broad handler in `run_transform`; no `except Exception` in transformers outside the field-map engine; classifier body has no `str(`/`in <str>`; no `.get(<str>,<str>).lower()` column reads outside `columns.py`; only CRITICAL modules assign `context.<attr>`); (6) **schema-drift matrix** (S13): for each bundled config × entity × guarded column, drop the column and assert the declared outcome; (7) **sentinel PII** sweeps over every copy/record path. Real drops for measurement live at `C:\Users\shan.peiris\source\repos\roster-validation\partners\<district>\<date>` (Unity `20260922`, SD51 `20260918`) — run locally only, with `DISTRICTSYNC_DATA_DIR` set and a SHORT temp output path (MAX_PATH tears the commit otherwise), never committed.

## Execution scaffolding for the implementing agents

- **Per slice:** one `claugentic-dev-harness:implementer-architect` (Opus, effort high; xhigh for S4/S9/S10) on an isolated branch `claude/0053-s<n>-<slug>`; `claugentic-dev-harness:architect-reviewer` audit before land; owner merges. Before editing: `git fetch origin && git merge --ff-only origin/main`; load `districtsync-design` for any `src/ui_flet` change.
- **Gates (canonical DoD, CLAUDE.md + WORKFLOW.md §DoD — deltas only here):** run `bandit -r src/ -q -c pyproject.toml` AFTER the last edit (B105 fires on names); `mypy src/ --exclude src/ui_flet --platform linux` when a Windows-only import is touched; CI's three-OS result read and QUOTED (`gh pr checks --watch` in the background, `PIPESTATUS`); flip the slice's `failure-policy.md` Status rows, `ARCHITECTURE_TREE`, `DECISIONS`, `CHANGELOG [Unreleased]` in the same change.
- **Parallelism:** S0 alone → S1 → S2 → S3 → S4 → **release** → then {S5 → S6} ∥ {S9 → S10 → S11} ∥ S12; S7/S8 after S6 and their owner gates; S13a after S10–S12, S13b after S13a; S14/S15 last.
- **Release note (R, recommended #2):** a scheduled task runs the exe PATH saved at schedule time (`src/scheduler/windows.py:627`). The S4 release reaches a district's nightly only if the new exe REPLACES the old one at that path, or the schedule is re-saved from Settings. Say so in the CHANGELOG and the partner upgrade note; it is not a code change here.
- **Commit the plan file at draft** (WORKFLOW Stage 2) — it is currently untracked; the owner decides when.
- **Owner gates are separate steps, not slice ACs:** Gate A (before S1) — approve §3 + §5 of the standard; decide D1–D4. Gate B (before S7/S8) — D4/D5. Gate C (before S13's BLE narrowing decisions) — none; annotate only. Gate D (before S15) — D13. Q5 is SENT BY THE OWNER (D2), whenever; S14 waits on the answer.
- **Verify-before-implement list** (claims the refuters could not confirm; the implementer checks and records in the PR): the one-night decay of the vanished alarm (G11); whether `enrollments.py:159-161` is reachable (Classes' KeyError may fire first; pandas 2 raises `ValueError` not `MergeError` on dtype mismatch); that CourseInfo/StudentCourses/StudentAttendance publish no `TransformContext` state (only `students.py:47`/`classes.py:49` confirmed); whether SD51's Family loss was a missing `Email Address` column or blank values (decides S6's reclassification); the exact count of test call sites for the changed signatures.

## Decomposition (slices)

Each slice lands **complete in one ≤1M-context session, no debt**. Sizes: S/M/L.

- [ ] **S0** — the standard, Q5, and a truthful FAQ (docs only) · lands complete because it changes no code and every row is marked ENFORCED/PLANNED truthfully
- [ ] **S1** — typed ETL errors; classification by type only · no delivered-byte change; only the recorded category becomes precise
- [ ] **S2** — per-entity outcome ledger + declared criticality + record key · no behaviour change; the reader is not wired yet
- [ ] **S3** — reader side: PARTIAL verdict + category-aware failure copy (inert until S4)
- [ ] **S4** — the entity bulkhead (the Unity fix) + creator gate + smoke-test tightening + partner docs made true → **cut release**
- [ ] **S5** — every Convert attempt leaves exactly one run record
- [ ] **S6** — own-file missing-mapped-column observation (advisory)
- [ ] **S7** — (owner-gated D4) config-declared file/column labels in copy and record
- [ ] **S8** — (owner-gated D5) standing warning while an enabled entity produces nothing
- [ ] **S9** — one column resolver; finish the `context.py` migration; delete the conftest mask
- [ ] **S10** — fail-closed conformance: `require_columns`, no raw `KeyError`, no partial ship
- [ ] **S11** — fail-open conformance: every (d)/(e) posture records a note
- [ ] **S12** — config typo visibility (origin-keyed)
- [ ] **S13a** — architecture fitness functions, justified broad excepts, one config-count constant
- [ ] **S13b** — the schema-drift matrix (own CI time budget) *(R sizing)*
- [ ] **S14** — (partner-gated) record SpacesEDU's Q5 answer; apply the promotion rule
- [ ] **S15** — (owner-gated D13) CLAUDE.md becomes rules + index

---

## Spec (per slice)

### S0 — `docs/developer/failure-policy.md`, Q5, and a truthful partner FAQ (docs only) [M] · deps: none

**Files:** `docs/developer/failure-policy.md` (NEW, ≤300 lines, tables first, modelled on `output-contract.md`'s graded tables) · `docs/developer/output-contract.md` (Open owner questions: add **Q5 after Q4** — the section already says "Settle Q1b FIRST" at :611, so Q5 carries its own priority note rather than displacing that *(R15)*) · `tests/test_output_contract_doc.py` (`_EXPECTED_QUESTION_COUNTS` + any heading pins *(R15)*) · `docs/partner/faq.md:19-21` · `docs/partner/help-centre-myedbc-districtsync-guide.md` (:175 false "extra columns ignored" claim + the missing-file passage) · `docs/claugentic-DECISIONS.md` · `docs/claugentic-ROADMAP.md` · `docs/claugentic-INVARIANTS.md` (pointer rows only) · `docs/claugentic-standards/CANDIDATES.md` · `CLAUDE.md` (+1 line under Engineering Principles) · `docs/developer/adding-transformer.md`, `adding-district.md` (one checklist line each).

**Design.**
1. **failure-policy.md sections:** §0 Status legend (`ENFORCED` / `PLANNED (0053 Sn)`; every row describes TODAY truthfully — e.g. "row_filters column missing → the whole RUN fails today; PLANNED entity scope for ISOLATABLE entities, S4") · §1 P1 asymmetric-risk rule (H1–H4 as restated in *Approach*, with the "detected fault" precondition on H2) · §2 layer stack + scope ladder (cell → column → entity → run) and who may widen it (only the orchestrator) · §3 criticality table, one parseable row per registry entity: `entity | criticality | rationale | promotion evidence | depends_on` — per D1 (decided): Family, StudentAttendance, CourseInfo, StudentCourses ISOLATABLE; Students, Staff, Classes, Enrollments CRITICAL; the promotion/demotion rule; the Q5 footnote (CourseInfo and StudentCourses are isolatable by OWNER DECISION ahead of partner evidence — say so in the rationale column; Q5d, whether they must arrive together, is open and each may ship without the other) · §4 never substitute · §5 missing-column matrix (a)–(e) keyed on WHAT THE COLUMN GUARDS + the **site catalogue** (below) · §6 typed errors + vocabularies (one parseable row per `RunErrorCategory`/`OutcomeKind`/`OutcomeReason` member; the scheduler's `MSG_*`/`_fail` named as the exemplar) · §7 surfacing, record keys, symmetric sinks (P7/P8/P12) · §8 privacy and labels (P9) · §9 column resolution (P10) · §10 source observation never enforces (P11) · §11 layering + justified broad excepts (P16) · §12 checklists (new entity / new config knob / new district) + DoD delta · §13 "choose your posture" decision tree (five questions: could this widen PII? does the column key an identity/join? is the output field importer-required? is there a recoverability argument for open? which entity scope contains the fault?) · §14 patterns in use, each with its in-repo exemplar.
2. **§5 site catalogue** (file:line · class · Status · target): `apply_row_filters` base.py:458-494 (a) conforms → typed S1 · `students.py:136-140` (b) · `students.py:282-286` (b) · `grades.py:393-397` (a) raw KeyError → **S1** · `grades.split_by_homeroom_grades`'s grade-column `KeyError` (a) → S10 *(R15)* · `students.py:193` grade-column resolution for the student scope → S9 *(R12)* · `enrollments.py:38-45` class_artifacts (b) conforms · `classes.py:384-392` raw pandas KeyError (b) → S10 · `classes.py:398-402` second raw KeyError on `staff_df[[teacher_id_col, LAST_NAME]]` (b) → S10 · `enrollments.py:159-161` partial ship (b) DEFECT → S10 · `classes.py:155`, `enrollments.py:116/:312` homeroom literal → S9 · `staff.py:273-275` silent (e) DEFECT → S11 · `staff._merge_roster` :325-353 silent → S11 · `base.filter_to_active` :366-368 fail-open unfiltered (e) → S11 · `enrollments.py:290-293` silent → S11 · `blended._teacher_from_frame` :551 / `_build_teacher_name_map` :591 silent vs `_build_grade_map` :654 warn → S11 · `base.py:288-294` withdraw-date fallback INFO (e) → S11 · `base.py:295-301` all-Active default (e)/H1 → S11 + D10 · `apply_field_map` intended blank ~:753 (d) → S6 · `family._exclude_rows_without_email` (c) — the POSITIVE exemplar · `staff.filter_departed_staff` :277-299 (e) conforms · plan-0052 staff role sites (e) conforms · `student_attendance.py:349-350` unmapped `(Absent Code, Authorized)` raise (b)-shaped vocabulary error → entity-scoped from S4 (StudentAttendance is ISOLATABLE under D1).
3. **Q5 in output-contract.md**, placed after Q4 with a "highest blast radius — answer before any S14 promotion" note, in the existing verbatim-question + "The check" bench format, registered in `_EXPECTED_QUESTION_COUNTS` (the existing pin — NOT a new marker), split: **Q5a** each of Students/Staff/Family/Classes/Enrollments.csv ABSENT from the rostering zip — previously imported records unchanged / deactivated-unlinked-unenrolled / zip rejected? **Q5b** same for a header-only file. **Q5c** each standalone feed (CourseInfo, StudentCourses, StudentAttendance) absent on a night — any change? does the nightly check alert? **Q5d** must StudentCourses and CourseInfo arrive together? **Q5e** is a guardian missing from a DELIVERED `Family.csv` unlinked? State evidence AS evidence (faq :109/:113 row absence only; :128 "invalid format → skip entire file" hints; zips without Family.csv already ship). Neutral wording — the answer must not be steered. Plus a machine-readable `Q5-status: open` line that S4's parity test reads.
4. **faq.md:19-21 + help-centre passage restated to TODAY's actual branches** (`pipeline.py:339-342`, `:461-468`): (i) an output is omitted when EVERY file it reads is empty OR every row is filtered out; (ii) the whole run stops when Students produces nothing, when no required input is usable, or when a present file lacks a column a filter or join needs — nothing is written or delivered and SpacesEDU keeps the last good sync; (iii) a missing secondary file (e.g. ClassInformation) degrades an output rather than stopping the run. Remove the false Course Information example (Classes reads 5 files, `myedbc_mapping.yaml:171-176`). Fix help-centre :175.
5. **DECISIONS:** dated entry "2026-09-23 — failure is scoped to the entity; criticality is declared; conservative default until Q5"; adopt a `Supersedes:` line convention. Do NOT add a second "standing policies" register — `INVARIANTS.md` is that register: add one INVARIANTS row per P-rule pointing at its failure-policy section + dated DECISIONS entry.
6. **ROADMAP:** one "Plan 0053" heading cross-referencing (not duplicating) the items listed at the top of this plan to their slices.
7. **CLAUDE.md:** exactly ONE line: *"ETL failure policy (scope, criticality, missing-column matrix, typed errors, labels): `docs/developer/failure-policy.md` — read before adding a check, entity or config knob; pinned by `tests/test_failure_policy_parity.py` (from S2)."*
8. **CANDIDATES.md:** stage "a fail-closed check on an OPTIONAL unit scopes its failure to that unit; criticality is declared and restrictive by default; alarms about absence are level-triggered".

**AC:** §0–§14 exist; no ENFORCED row describes behaviour that does not exist (reviewer checks each against code) · §5 lists every site above with class + target · Q5 after Q4 with its priority note, sub-questions a–e, bench check each, `_EXPECTED_QUESTION_COUNTS` updated and `tests/test_output_contract_doc.py` green, `Q5-status: open` present · faq.md contains no statement contradicted by `pipeline.py:320-352`; help-centre :175 corrected · CLAUDE.md grows by exactly one line · `python scripts/check_no_emails.py` green; tree check passes (docs not indexed) · D1/D3/D4 (decided 2026-09-23) recorded in DECISIONS · **Gate A remainder:** owner approves §3 + §5 before S1 starts.

**Tests:** none (docs). Pins arrive with the code: S2 (criticality + vocabularies), S4 (FAQ ↔ ISOLATABLE + Q5 clause), S11 (site-catalogue bijection).

**Risks:** length (cap 300 lines); Q5 wording must be neutral; the FAQ is edited here and again in S4 (accepted — a false partner doc is worse than churn).

---

### S1 — Typed ETL error taxonomy; classification by type only [M] · deps: S0 (Gate A)

**Files:** `src/etl/errors.py` (NEW; stdlib only — no pandas/pipeline/transformer imports, so transformers can import it without a cycle) · `src/etl/pipeline.py` (delete `RunErrorCategory` :75-89 and `_classify_error_category` :743-766; raise `NoUsableInputError` at :945; rebase `DeliveryIntegrityError` :359 / `OutputWriteError` :378 onto `EtlError` IN PLACE) · `src/etl/extractor.py` (`ExtractionError` :41 → `EtlError`, category `INPUT_UNREADABLE`) · `src/etl/transformers/base.py` (`apply_row_filters` :483-488 raises `SourceSchemaError`; add `except EtlError: raise` ahead of the broad excepts in the field-map engine at ~:755/:786) · `src/etl/transformers/students.py` (:136-140, :282-286 → `SourceSchemaError(JOIN_KEY)`) · `src/etl/transformers/grades.py:393-397` (`SourceSchemaError(PII_SCOPE)` — moved here from S10 so the incident-critical (a)-class sites are all typed in one slice) · importers: `src/ui_flet/convert_result.py`, `src/ui_flet/screens/convert.py`, `tests/test_pipeline_delivery_integrity.py`, `tests/test_ui_flet_convert_result.py` (no re-export shim) · `tests/test_etl_errors.py` (NEW) · `tests/test_transform_base.py`, `tests/test_transform_family.py`, `tests/test_pipeline_run_store.py` · `ARCHITECTURE_TREE`, `failure-policy.md` §6 flips, `DECISIONS`, `CHANGELOG`.

**Design.**
- `class RunErrorCategory(StrEnum)` — moved verbatim, eight persisted values unchanged, plus `SOURCE_SCHEMA`, `INPUT_UNREADABLE`. `StrEnum`, not `(str, Enum)`: on Python 3.13 `str(member)` of a `(str, Enum)` yields `"Class.MEMBER"`, and the store persists via `str()`. Add ONE normalisation point where the record's `error_category` is written (`.value`) and a parity test that the `runs.error_category` column equals the JSON record's value for every member.
- `class EtlError(Exception)`: `default_category: ClassVar[RunErrorCategory] = RunErrorCategory.UNKNOWN`; `__init__(self, message, *, category: RunErrorCategory | None = None)` sets `self.category: RunErrorCategory = category or type(self).default_category`. (No ClassVar-overridden-by-instance — mypy rejects that.) The two existing carriers' raise sites are CHANGED from passing `.value` strings to passing enum members (see R14 below).
- `class GuardKind(StrEnum)`: `PII_SCOPE`, `JOIN_KEY`.
- `class SourceSchemaError(EtlError, ValueError)`: `__init__(message, *, entity: str, columns: tuple[str, ...], guard: GuardKind)`, all REQUIRED keyword-only; empty `columns` → `ValueError`; `default_category = SOURCE_SCHEMA`; `columns` hold CONFIG spelling. **Message text carries the missing config names + the COUNT of available columns — never the observed header list** (PII floor; also applies to the log line: the `Available: {sorted(df.columns)}` dumps at base.py:487, grades.py:396, students.py:139/285 are removed).
- `class NoUsableInputError(EtlError, RuntimeError)`: `NO_INPUT`; raised at pipeline.py:945 with the byte-identical message.
- `class ConfigLoadError(EtlError, ValueError)`: `CONFIG`. *(R4, re-reviewed)* Defined here, used by **Convert only** (S5 wraps `convert.py:271` so its screen copy and record both say CONFIG by type). The **pipeline's config block (`pipeline.py:856-881`) is left EXACTLY as it is**: it already records CONFIG via `_record_early_failure` and exits 1 (`tests/test_pipeline_run_store.py:821-842` pins `SystemExit(1)`); converting it to a raise would double-record and would defeat `humanize_config_error`'s type-based recognition of a missing `_base`/unreadable file (`config_editor.py:978-981`, `:1083`; pinned by `tests/test_ui_flet_config_editor.py:890`). The creator gate therefore keeps receiving the ORIGINAL exception types.
- `classify_error_category(exc)` PUBLIC: `EtlError` → its instance category; `FileNotFoundError` → CONFIG; `ValueError` → DATA; else UNKNOWN. No substring test. Both entry points must call this one function (S5 wires Convert).
- **Existing carriers pass `.value` strings today** *(R14)*: `DeliveryIntegrityError`/`OutputWriteError`'s raise sites are updated to pass enum members; `EtlError.__init__` additionally coerces a legacy `str` via `RunErrorCategory(category)` so a missed site fails loudly on an unknown string rather than silently.
- `reason_for(exc)` is NOT here — it lives in `outcomes.py` (S2), because `outcomes` imports `errors` and never the reverse.
- `apply_row_filters`: check ALL filter columns, raise ONE error naming every missing one.

**AC:** `pipeline.py` no longer defines `RunErrorCategory`/`_classify_error_category`; an AST sweep over src/ and tests/ finds no `ImportFrom` of `src.etl.pipeline` naming `RunErrorCategory` (a grep is vacuous — `convert.py:118-120` imports it inside a parenthesised block *(R13)*) · no classifier reads exception text (AST-pinned) · the Unity plain-report failure records `source_schema` (was `data`); an unparseable file records `input_unreadable` (was `unknown`); a torn/invalid mapping YAML still records `config` on the pipeline path exactly as today (block unchanged; `SystemExit(1)` pin green) and, from S5, `config` on Convert by TYPE; every other category unchanged · every existing `pytest.raises(ValueError, …)`, `pytest.raises(RuntimeError, match="No usable required input")` and `ExtractionError` test passes unmodified (`tests/test_pipeline_required_input.py:130,465`; `tests/test_pipeline_run_store.py:198,212,765`) **EXCEPT the tests this slice deliberately updates and lists in the PR** *(R3)*: `tests/test_student_rostering_grades.py:163` and `:287` pin `KeyError` and `:163-167` asserts the message lists the available columns — both change to `SourceSchemaError` (guard `PII_SCOPE`) and a missing-config-name + available-count assertion, because listing observed headers is exactly what this slice removes · SD74 snapshot, `test_contract.py`, `test_pipeline_parity.py`, config validation byte-identical; exit codes unchanged · store column == record JSON category for every member · no exception message or log line under src/etl contains an observed header list (sentinel test) · canonical DoD.

**Tests (`tests/test_etl_errors.py` unless noted):** recursive walk of `EtlError` subclasses — every concrete leaf has a category · no-text-matching twins: `classify(NoUsableInputError(...)) == NO_INPUT` but `classify(RuntimeError("No usable required input was loaded")) == UNKNOWN`; `SourceSchemaError` → SOURCE_SCHEMA vs plain `ValueError` → DATA; `ExtractionError` → INPUT_UNREADABLE; `FileNotFoundError` → CONFIG; `KeyError` → UNKNOWN · AST pin: `classify_error_category` body has no `str(` Call and no `in` Compare against a string Constant · constructing `SourceSchemaError` without entity/columns/guard → `TypeError`; empty columns → `ValueError`; `isinstance(…, ValueError)` · StrEnum persistence: `str(member) == member.value` for every member; store round-trip parity · `test_transform_base.py`: two missing filter columns both named in one error, config casing, guard PII_SCOPE; twin: all present → filters as before; a `SourceSchemaError` raised inside a field transform is NOT swallowed into a blank cell · `test_pipeline_run_store.py`: unparseable required file → `input_unreadable`; twin: monkeypatched transform raising `RuntimeError` → `unknown` · sentinel: a frame whose first "header" cell is `SENTINEL_PII` never appears in any raised message or captured log.

**Docs:** `ARCHITECTURE_TREE` (errors.py; pipeline/extractor lines) · `failure-policy.md` §6 → ENFORCED (taxonomy) · `DECISIONS`: "classification by type only; taxonomy lives in `src/etl/errors.py`; observed headers never in exception/log text" · `CHANGELOG`: "Run History distinguishes an unreadable export file and a missing column from other failures".

**Risks:** import churn (grep before landing); multiple inheritance must keep `main.py`'s generic handler and every `except ValueError` working (full suite); SD83's Staff row_filter failure now records `source_schema` — update any test asserting `data` for that shape and list it.

---

### S2 — Per-entity outcome ledger, declared criticality, record key (no behaviour change) [M] · deps: S1

**Files:** `src/etl/outcomes.py` (NEW; stdlib only) · `src/etl/pipeline.py` (`run_transform` :258-356 gains `*, ledger`; `TransformOutputs` :238 gains `outcomes` as 5th field → switch the two production unpacks `pipeline.py:953`, `convert.py:315` to attribute access; `build_run_record` :566 gains `*, entity_outcomes`; the four sinks :653/:717/:1062/:1099; `PipelineResult` :92 gains `entity_outcomes: tuple[EntityOutcome, ...]` — **REQUIRED, no default** *(re-review: an empty default would let a failed entity pass the creator gate, violating P8)*; `ROSTER_ANCHOR_ENTITY` :61 moves) · `src/etl/transformer.py` (`DataTransformer.data_errors_mark()` / `rollback_data_errors(mark)` beside :79-81) · `src/ui_flet/screens/convert.py` (build the ledger **right after `config.to_raw_dict()`**, before `load_data` — same point as the CLI *(R7)*; pass to `run_transform`; `ConvertResult.entity_outcomes` on every result; `_record_manual_run` gains required `entity_outcomes`) · `src/ui_flet/convert_result.py` (`ConvertResult.entity_outcomes: tuple[EntityOutcome, ...] | None` — REQUIRED keyword; `None` is an explicit "no ledger existed", never a default) · `tests/test_etl_outcomes.py` (NEW) · `tests/test_failure_policy_parity.py` (NEW) · `tests/test_pipeline_run_store.py`, `tests/test_pipeline_parity.py`, `tests/test_main_helpers.py` (TransformOutputs unpacks), `tests/test_identity_pii_guards.py`, `tests/conftest.py:760`, `tests/test_pipeline_required_input.py:324/:341/:382` (every direct `build_run_record`/`run_transform` caller — grep both before starting *(R6)*) · **every `PipelineResult(` and `ConvertResult(` constructor site** — making `entity_outcomes` required touches ~20 `PipelineResult(` sites across five test files and ~36 `ConvertResult(` sites (26 in tests, 10 in `screens/convert.py`); grep both before starting and list them in the PR. Both fields are declared `field(kw_only=True)` — every other field on those dataclasses has a default, and Python rejects a required positional field after defaulted ones · docs.

**Design.**
- `ENTITY_CRITICALITY: Final[Mapping[str, EntityCriticality]]` (MappingProxyType) over all 8 registry entities per **D1 (decided: Family, StudentAttendance, CourseInfo, StudentCourses ISOLATABLE; Students, Staff, Classes, Enrollments CRITICAL)**; each row a one-line rationale comment citing faq :109/:113, output-contract or Q5; a `DEPENDS_ON: Mapping[str, frozenset[str]]` beside it holding CODE dependencies only *(R10)*: `Enrollments→{Classes, Students}` (`context.class_artifacts`, `active_student_ids`), `Classes→{Students}` (`classes.py:143` → `filter_to_active` reads `active_student_ids`; found by the S0 writer), `Family→{Students}`, `StudentCourses→{Students}`. (S0 also corrected several citations — `staff.filter_departed_staff` :250-307, classes merges :389-393/:400-404, attendance raise :349-353, class-artifacts check :37-45, pipeline failure sink :1089; `failure-policy.md` §5 carries the verified lines — prefer it over this plan's line numbers.) StudentCourses does NOT depend on CourseInfo in code (`student_courses.py:112-114` falls back when history is empty) — whether the two must ARRIVE together is a delivery question (Q5d), not a dependency. `criticality_of(name)` → CRITICAL for anything unlisted.
- `EntityOutcome.__post_init__` refuses: BUILT with rows ≤ 0 or reason ≠ NONE; EMPTY/FAILED/NOT_RUN with rows ≠ 0; reason ∉ `VALID_REASONS[kind]`; negative rows.
- `OutcomeLedger(configured: Sequence[str])`: `record(o)` refuses duplicates and unknown entities; `mark_not_run(entities)`; `finalize_aborted()` marks every unrecorded configured entity NOT_RUN/RUN_ABORTED (so a raise BEFORE the loop still yields a complete ledger — never `{}`); `outcomes` in configured order; `complete()` raises if any configured entity lacks an outcome.
- **Two row numbers, two defined meanings (documented in `build_run_record`'s docstring):** the flat per-entity count keys keep EXACTLY today's `_counts_from_outputs` semantics (`pipeline.py:1102`, `convert.py:329` — whatever was in `outputs` when the record was built, which is NOT necessarily 0 on a failed run *(R7)*); `entity_outcomes[e].rows` = rows the TRANSFORM produced. They agree for BUILT entities on a successful run; the docstring says which to read for what, and Home keeps reading the flat keys.
- **A failure BEFORE the loop** *(R7, re-reviewed)*: BOTH entry points create the ledger at the same point — immediately after `config.to_raw_dict()`, before `load_data` — and the failure sink always calls `ledger.finalize_aborted()`, so an `ExtractionError`, a `NoUsableInputError` or any pre-loop raise records every configured entity NOT_RUN/RUN_ABORTED on BOTH paths (S2/S5 parity ACs depend on this). Only an attempt that ended before the ledger EXISTS — config load, output-folder pre-flight — records `entity_outcomes = None`.
- `run_transform` records at the existing branches: :325/:330 → EMPTY/NO_SOURCE_FILES_DECLARED; :341 → EMPTY/SOURCE_FILES_EMPTY; :348 → EMPTY/NO_ROWS_AFTER_TRANSFORM; success → BUILT(len). In THIS slice a raise at :345 is caught only to `ledger.record(FAILED(reason_for(exc)))` + `mark_not_run(rest)` then **bare `raise`** — no isolation yet.
- `build_run_record(..., *, entity_outcomes: Sequence[EntityOutcome] | None)` REQUIRED keyword-only; writes `record["entity_outcomes"] = outcomes_to_record(...)`, or `None` only when no ledger existed (the attempt ended at config load or output pre-flight). Docstring: additive JSON beside `run_as`; no DDL; no `user_version` bump; why (ROADMAP:50); older exes ignore the key and degrade to today's rendering.
- `outcomes_from_record` TOTAL: non-dict → (); blank/non-str key dropped; unknown kind/reason → FAILED/TRANSFORM_ERROR (errs toward a warning); illegal combo dropped; never raises.

**AC:** every CLI/scheduled success record carries `entity_outcomes` whose keys equal `configured_entity_order(...)` exactly; log line and store share one dict · SD51-shaped run → Family EMPTY/NO_ROWS_AFTER_TRANSFORM; Unity plain-report FAILED record → Family FAILED/MISSING_SOURCE_COLUMN, Students+Staff BUILT, Classes+Enrollments NOT_RUN; a pre-loop raise → every entity NOT_RUN/RUN_ABORTED · Convert records carry the same outcomes the CLI would; early-failure and delivery-only records carry `None` · no CSV byte changes; existing store tests unchanged · `entity_outcomes` and `ledger` keyword-only, no default (signature test) · the record key does not collide with the flat count keys (key-set test) · canonical DoD.

**Tests:** `test_etl_outcomes.py` — every illegal state refused with a legal twin; criticality totality `set(ENTITY_CRITICALITY) == set(TRANSFORMER_REGISTRY) == set(pipeline._RECORD_ENTITY_KEYS)`; `criticality_of("HandDropped") is CRITICAL`; anchor is CRITICAL; every entity in `DEPENDS_ON` values is CRITICAL (an ISOLATABLE entity may not be depended on); ledger duplicates/complete/finalize_aborted twins; `outcomes_from_record` totality over None / list / `{"": x}` / unknown kind / negative rows; lossless round trip · AST fitness: every attribute ASSIGNMENT `context.<attr> = …` under `src/etl/transformers` (excluding `data_errors`; method CALLS such as S11's `context.record_outcome_note(...)` are not publications and are ignored *(#20)*) is in a CRITICAL entity's module; positive twin finds `students.py:47`, `classes.py:49` · `test_failure_policy_parity.py` — §3 table == `ENTITY_CRITICALITY` (+ depends_on); §6 rows == enum members; non-vacuity ≥8 rows; doctored-doc negative twin; assertion messages cite the section · `test_pipeline_run_store.py` — EMPTY/BUILT/FAILED+NOT_RUN shapes; required-kwarg signature test · `test_pipeline_parity.py` — CLI ≡ Convert `entity_outcomes`.

**Docs:** `ARCHITECTURE_TREE` (outcomes.py; pipeline/transformer lines) · `failure-policy.md` §3/§6/§7 → ENFORCED (recording) · `DECISIONS`: "entity_outcomes is an additive record key; criticality per D1; reader lands before producer; two row meanings".

---

### S3 — Reader side: PARTIAL verdict + category-aware failure copy on Home, Run History, Convert (inert until S4) [M] · deps: S2

Load the `districtsync-design` skill first.

**Files:** `src/ui_flet/failure_copy.py` (NEW, COUNTED, no flet) · `src/ui_flet/humanize.py` (**`ENTITY_LABELS`/`SIZE_NOUNS` MOVE here from `home_status.py:79/:115`** *(R8)* — `home_status` imports `failure_copy`, so `failure_copy` importing `home_status` would be a cycle; `home_status` re-imports the maps from `humanize`; never a third label map — `creator.py:275`'s second map is a ROADMAP note) · `src/ui_flet/home_status.py` (`LatestReason` :484, `classify_latest_reason` :504, `_REASON_VERDICTS`, `derive_home_status`, the FAILED_ETL detail; **PARTIAL is derived from the record `_counts_source` walks back to** — a delivery-only record inherits the newest BUILD record's outcomes, so a deliver-from-disk after a partial build cannot turn Home green *(R9; `home_status.py:819`)*) · `src/ui_flet/run_history.py` (`_status_label`, `derive_history_banner`) · `src/ui_flet/convert_result.py` (`summarize` reads outcomes; `convert_error_copy()` RETIRED → `error_card_copy(exc)`; its existing FAILED branches read from `FAILED_CATEGORY_COPY` so there is ONE copy source) · `src/ui_flet/screens/convert.py` (`_on_error` :817-831 renders `error_card_copy(exc)`) · tests: `test_ui_flet_failure_copy.py` (NEW), `test_ui_flet_home_status.py`, `test_ui_flet_run_history.py`, `test_ui_flet_home_history_parity.py`, `test_ui_flet_convert_result.py`, `test_failure_policy_parity.py` · `docs/claugentic-PRODUCT.md` (verdict-state list), `ARCHITECTURE_TREE`, `failure-policy.md`, `DECISIONS`, `CHANGELOG`.

**Design.**
- `entity_phrase(entity)` uses `humanize.ENTITY_LABELS`/`SIZE_NOUNS` (moved there in this slice) as the ONE vocabulary; unknown key → "one of your files", never echoed.
- `outcome_sentence(outcome, *, delivered: bool)` TOTAL over valid (kind, reason). FAILED/MISSING_SOURCE_COLUMN: *"<Family contacts> were left out of this sync: their export file is missing a column this district's mapping needs — often because a different report was saved under the same name. Everything else <was delivered | completed>. Re-export that file and the next sync picks it up automatically."* No filename/column interpolation (S7).
- `FAILED_CATEGORY_COPY: Mapping[RunErrorCategory, tuple[str, str]]` TOTAL over members except NONE (NONE raises). Only NO_INPUT and INPUT_UNREADABLE mention the input folder. SOURCE_SCHEMA: an export file is missing a column the mapping needs. CONFIG: the district mapping could not be read. The pre-write tail is ONE of two fixed strings chosen by a bool — *"Nothing was sent to SpacesEDU this time."* (delivery was attempted) / *"Your output folder was not changed."* — never an assertion about what SpacesEDU holds. Zero interpolation.
- `home_status`: `LatestReason.PARTIAL`; precedence status → sftp → PARTIAL (success ∧ `failed_entities(outcomes_from_record(record))` non-empty) → anomalies → data_errors → CLEAN; `_REASON_VERDICTS[PARTIAL] = WARNING`; sits where ANOMALY sits in `derive_home_status`; fix CTA → Run History. Headline *"Your roster synced without family contacts"* / *"…completed without…"* when not delivered / *"…without 2 of your files"*. FAILED_ETL detail from `FAILED_CATEGORY_COPY[record["error_category"]]`, generic fallback on missing/unknown (total). Grep EVERY `LatestReason` consumer (quick_actions, schedule-attention precedence, history banner, `_status_label`) and give PARTIAL an explicit branch; the verdict-totality test goes red first. Records without the key classify exactly as before.
- `run_history._status_label`: "Delivered · 1 file skipped" / "Completed · 1 file skipped"; banner via the shared classifier.
- `convert_result.summarize`: BUILT_NOT_DELIVERED and every FAILED status keep precedence; a success-shaped status with failed outcomes → WARNING + outcome sentence (+ the data-error clause as a second sentence).
- Design rules: toned WARNING band, one filled primary per state, tokens only, AA test green.

**AC:** synthetic record `{status: success, entity_outcomes: {Family: failed/missing_source_column, …built}}` → PARTIAL/WARNING on Home, "1 file skipped" in Run History, WARNING banner · a delivery-only record following that build record still classifies PARTIAL (walk-back twin: following an all-BUILT record → CLEAN) · `home_status` imports no label map from itself and `failure_copy` imports nothing from `home_status` (import-cycle pin) · precedence matrix pinned (failed+outcomes → FAILED_ETL; sftp failed → FAILED_DELIVERY; anomalies+FAILED outcome → PARTIAL; data_errors+FAILED → PARTIAL with clause kept) · records without the key → byte-identical HomeStatus and Run History row · a failed record with `source_schema` shows SOURCE_SCHEMA copy on Home and Run History; Convert's on_error card for SOURCE_SCHEMA/CONFIG/DATA never mentions the input folder · no copy contains a filename, column, path, unknown entity key or exception text (sentinel `SENTINEL_PII C:\secret`) · Home/Run History/Convert FAILED copy per category is identical (parity) · canonical DoD incl. design-skill UI DoD.

**Tests:** as listed per file above, incl. totality of `outcome_sentence` and `FAILED_CATEGORY_COPY`; no `{` in any string; only NO_INPUT/INPUT_UNREADABLE contain "input folder"; PARTIAL positive/negative twins; `_REASON_VERDICTS` totality; legacy-record snapshot; `error_card_copy` maps each typed exception; §6 ↔ copy parity.

**Docs:** `failure-policy.md` §7/§8 → ENFORCED (surfacing) · `PRODUCT.md` adds the "part of the sync was skipped" state · `DECISIONS`: "PARTIAL is a WARNING rung below FAILED_DELIVERY and above ANOMALY; `convert_error_copy` retired; reader before producer" · `CHANGELOG`: "Failure messages now say what kind of problem it was instead of always pointing at the input folder".

---

### S4 — The entity bulkhead: an ISOLATABLE entity fails alone; everything else fails the run as today (the Unity fix) [L] · deps: S3 → **cut a release after landing**

**Files:** `src/etl/pipeline.py` (`run_transform` isolation branch; `run_pipeline` wiring; dry-run printout) · `src/ui_flet/screens/convert.py` (`convert_job`: outcomes on every result) · `src/ui_flet/config_editor.py` (`gate_outcome_for` :1033-1091) · `scripts/ci_flet_pack_smoke.py` (:897/:903 per-entity assertion tightened — see 5) · `tests/test_pipeline_entity_isolation.py` (NEW) · `tests/test_contract.py` (`_create_unitychristian_plain_report_inputs`) · `tests/test_pipeline_parity.py`, `tests/test_pipeline_delivery_integrity.py`, `tests/test_ui_flet_config_editor.py`, `tests/test_failure_policy_parity.py` (FAQ ↔ ISOLATABLE + Q5 clause) · `docs/partner/faq.md`, `help-centre-myedbc-districtsync-guide.md`, `troubleshooting.md` · `docs/developer/output-contract.md` (Delivery envelope "Rostering bundle" row + changelog row + MINOR bump 2.6.0 → 2.7.0 *(verify current version)*) · `INVARIANTS`, `failure-policy.md`, `CLAUDE.md` (two in-place sentence edits: Exit codes; row_filters), `DECISIONS`, `CHANGELOG`.

**Design.**
1. **`run_transform` around :345:** `mark = transformer.data_errors_mark()`; `except Exception as exc:  # noqa: BLE001 — entity bulkhead, failure-policy §2` → `ledger.record(FAILED(reason_for(exc)))`; if `criticality_of(entity) is CRITICAL`: `mark_not_run(rest)` + bare `raise` (S2's behaviour: same object, category, exit code); else `transformer.rollback_data_errors(mark)` (a dropped entity's per-row ledger must not inflate "N data warnings"), `logger.error("ENTITY NOT BUILT [%s] reason=%s — left out of this run; every other entity continues.", entity, reason, exc_info=True)`, `continue`. `BaseException` never caught. There is NO dependent-withholding branch *(R10)*: S2's test already guarantees no entity in any `DEPENDS_ON` value is ISOLATABLE, so a FAILED entity can never have a dependent — the structural pin replaces runtime code that could never execute.
   **Single-entity configs** *(R11)*: after the loop, if EVERY configured entity FAILED (nothing BUILT or EMPTY — e.g. `sd51attendance` with a broken absence code), `raise first_exc` (the first isolated exception, traceback preserved) instead of letting `check_delivery_integrity` report NO_OUTPUT — isolation must never turn a precise failure into a vaguer one. Pinned by a test.
2. **`compute_anomalies` / `_check_anomalies`: UNCHANGED.** A failed Family with a previous `Family.csv` still produces the vanished-entity anomaly; on the nightly PARTIAL outranks ANOMALY; on Convert the existing NEEDS_ANOMALY_ACK gate still fires and its prompt copy carries the not-built reason (`outcome_sentence`). No `not_built=` parameter, no new gate (D7 resolved by reuse).
3. **`check_delivery_integrity`: UNCHANGED floor.** Missing Students → INCOMPLETE_ROSTER; previous output untouched. (An all-failed run never reaches it — §1's re-raise fires first — so NO_OUTPUT keeps today's meaning: every source empty, nothing to build.) No change to `save_all`/`archive_stale_outputs`/manifest: a failed Family is absent from `outputs`, so its previous CSV is archived out of the glob and `DataLoader.output_filenames(outputs)` cannot carry it. Partial run = `status success`, `error_category none`, exit 0 (D3).
4. **`deliver_job` is UNCHANGED** *(R1 — blocker, the reviewer is right)*. The earlier idea of filtering deliver-from-disk by the newest record's BUILT set would, after a CRITICAL failure (record: most entities NOT_RUN; previous good files still on disk), ship `Students.csv` alone — the plan's own H2 violation — and make the deliver button, the "files last built" caption (`convert_output.py:16-29`, :549-600) and the payload disagree; records also mix districts and carry no output folder. The failed entity's stale CSV is already archived out of the folder on the same run, which is the guarantee. What S5 adds instead: the delivery record carries `entity_outcomes=None` and Home's PARTIAL walks back to the build record (S3), so a resend cannot paint a partial build green.
5. **Dry run:** print not-built entities in a DISTINCT shape — `  ! not built: <Entity> (<reason>)` — never `<Entity>: …`, and **tighten `scripts/ci_flet_pack_smoke.py` :897/:903** *(R5)*: today it only asserts each entity NAME appears, which a "Family: NOT BUILT" line would satisfy vacuously; it must assert the BUILT-line shape (`<Entity>: <n> rows`) per entity with a negative twin over a not-built line. Do NOT reword the banner pinned by `_smoke_dry_run` :882.
6. **Creator activation gate:** `gate_outcome_for` returns PASSED **only when every configured entity's outcome is BUILT or EMPTY**; any FAILED/NOT_RUN outcome, or a result whose `entity_outcomes` is `None`, → `GateState.FAILED` with a fixed bounded note (entity phrase only). Original exception types still reach `humanize_config_error` unchanged (S1 leaves the pipeline's config block alone). Without this the creator's dry-run test conversion (`job_runner.py:250`) would PASS a self-service mapping whose Family fails and `activation_allowed` would accept it.
7. **`convert_job`** follows the CLI order; outcomes on EVERY result; `_record_manual_run` receives them.
8. **Partner + contract docs made true:** faq :19-21 rewritten to the criticality rule — a problem with a CRITICAL file stops the run and nothing is sent (SpacesEDU keeps the last good sync); a problem with an ISOLATABLE file's export leaves that CSV out of that night's delivery, sends everything else, and shows a warning until a correct export arrives — with the clause *"what SpacesEDU does with records linked by an earlier delivery of that file is pending confirmation"* while `Q5-status: open`. Never promise "last good sync kept" for a merely missing/empty file. output-contract "Rostering bundle" row: `Family.csv` may be absent on a flagged night (pending Q5). troubleshooting: "Family contacts weren't included". CLAUDE.md: edit the Exit-codes bullet and the row_filters sentence in place; no new lines.
9. **INVARIANTS entry:** the bulkhead lives ONLY in `run_transform`; unknown entities are CRITICAL; only CRITICAL entities publish `TransformContext` state; a failed entity's output is dropped whole and never substituted; a partial run is never green.
10. **Measurement (local, never committed):** Unity `20260922` drop with `DISTRICTSYNC_DATA_DIR` set and a short temp output path → expect exit 0; Students 667, Staff 46, Classes 147, Enrollments 2,738 (the overlay run's counts, same v3.25.0 code); no `Family.csv`; record Family FAILED/missing_source_column.

**AC:** Unity plain-report fixture (18-column contacts file, planted non-guardian marker name): `run_pipeline` returns normally, CLI exit 0; Students/Staff/Classes/Enrollments byte-identical to the same fixture with Family removed from `enabled_entities`; no top-level `Family.csv` (previous under `archive_<ts>/`); mocked SFTP manifest excludes it; marker in NO output file; record `status success`, `entity_outcomes.Family = failed/missing_source_column`; Home → PARTIAL · Enhanced twin (`_create_unitychristian_inputs`): Family BUILT with guardian rows only; every existing Unity assertion passes · a SECOND consecutive plain-report run is still PARTIAL after the first archived `Family.csv` · Convert with a previous `Family.csv` and a failed Family still reaches NEEDS_ANOMALY_ACK; the prompt names the not-built reason · `deliver_job` is byte-identical to today (its tests unchanged) · a single-entity config whose only entity fails re-raises that entity's own exception (record: that entity FAILED, category from the exception — not NO_OUTPUT); twin: a two-entity config with one failure is PARTIAL · the packed-exe smoke fails on a fixture where one entity is not built (negative twin) and passes on the clean fixture · a CRITICAL failure (SD83-shaped Staff with `Prefix` missing; a raising Students or Enrollments transform) behaves as today: same object propagates (`is`), nothing written or uploaded, previous outputs untouched, exit 1, failed record shows that entity FAILED and the rest NOT_RUN · creator test conversion with a failed entity → `GateState.FAILED`; clean → PASSED · CLI ≡ Convert outputs + outcomes over the plain fixture · SD74 snapshot byte-identical; 20 contract configs unchanged; config versions and `SUPPORTED_CONFIG_MINOR=13` untouched; 20-config pin holds; `_DISTRICT_SETUP` not extended with the plain variant · `test_output_contract_doc.py` passes with the new version row · canonical DoD incl. `mypy --platform linux`.

**Tests:** `tests/test_pipeline_entity_isolation.py` — parametrised over `ENTITY_CRITICALITY` (ISOLATABLE raise → others written + FAILED; CRITICAL raise → `pytest.raises` with `is` + untouched output dir); Family raising plain `RuntimeError` isolated as TRANSFORM_ERROR; data_errors rollback removes only the failed entity's entries (twin: a Students data error survives); the two-night non-decay test; the all-failed re-raise (single-entity config) with its two-entity PARTIAL twin; AST pin: exactly one broad handler in `run_transform`, no `except Exception` in `src/etl/transformers` outside `apply_field_map`/`_apply_transform_resilient` · `test_contract.py`: the plain-report builder with the assertions above · `test_ui_flet_config_editor.py`: gate twins (FAILED / NOT_RUN / `None` → FAILED; all BUILT-or-EMPTY → PASSED) · the Convert screen's test module *(#18 — `tests/test_ui_flet_convert.py` does not exist; grep for the module that already exercises `convert_job`, else create `tests/test_ui_flet_convert_job.py`)*: ack-gate still fires with the reason in the prompt · `scripts/ci_flet_pack_smoke.py`: the tightened per-entity assertion + negative twin · the new `troubleshooting.md` entry's quoted literals registered in the existing partner-doc copy-parity table *(#19)* · `test_pipeline_parity.py` · `test_failure_policy_parity.py`: faq's isolatable sentence names exactly the ISOLATABLE entity phrases and carries the pending clause while `Q5-status: open`; doctored-FAQ negative twin.

**Docs:** as listed; `failure-policy.md` §2/§3/§4 → ENFORCED · `DECISIONS`: "ISOLATABLE set per D1 with rationale; the pending Q5; why the rest stay CRITICAL; compute_anomalies deliberately unchanged" · `CHANGELOG`: "A problem with the family-contacts export no longer stops the whole roster sync".

**Risks:** as in *Risks & mitigations* (guardian unlink unknown; loud-WARNING vs failure for a Family code bug; CI-pinned dry-run banner). The single-entity edge is handled by §1's all-failed re-raise, so a lone failing entity records its own category, never NO_OUTPUT.

---

### S5 — Every Convert attempt leaves exactly one run record; the manual sink has no defaulted outcome [S] · deps: S4

**Files:** `src/ui_flet/screens/convert.py` (`convert_job` :215-447; `_record_manual_run` :517-525) · `tests/test_convert_failure_record.py` (NEW) · `tests/test_pipeline_parity.py`, `tests/test_ui_flet_home_status.py` · docs.

**Design.** (0) `deliver_job`'s record carries `entity_outcomes=None` (a delivery is not a build); Home's PARTIAL comes from the walk-back to the build record (S3) *(R9)*. (1) Wrap `config = load_config(config_name)` (:271) in a narrow `except (FileNotFoundError, ValueError, yaml.YAMLError) as exc: raise ConfigLoadError(str(exc)) from exc` (S1's type, so screen and record both say CONFIG *(R4)*), record failed/CONFIG/`entity_outcomes=None`, bare `raise` (the CLI's block at `pipeline.py:869-881` already records CONFIG itself and is left untouched by S1; this gives Convert the same outcome). (2) Unrecorded by design, per the docstring at :230-235: unset-output-folder `ValueError`, OUTPUT_FOLDER_UNUSABLE return, NO_INPUT, NEEDS_ANOMALY_ACK — enumerated in DECISIONS as the deliberate non-records. (3) ONE `try:` from `config.to_raw_dict()` through `save_all`/archive/quality report, **ending BEFORE the SFTP leg** (delivery outcomes already have their own recorded path — BUILT_NOT_DELIVERED); `except Exception as exc:  # noqa: BLE001 — symmetric failure sink (failure-policy §7); always re-raised` → `_record_manual_run(ConvertResult(failed shape, entity_outcomes=ledger.outcomes), status="failed", error_category=classify_error_category(exc).value, …)` inside `contextlib.suppress` (recording is non-fatal) → bare `raise` so `JobRunner.on_error` gets the SAME object. Paths that already record and RETURN are untouched → exactly one record per attempt. (4) `_record_manual_run`: `status` and `error_category` REQUIRED keyword-only; all call sites explicit; docstring's "deliberate asymmetry" paragraph updated. (5) Documented edge: a raise after `save_all` (e.g. in the quality report) records `failed` though files were written — honest, the attempt did not complete.

**AC:** a `convert_job` whose CRITICAL entity raises → exactly ONE record (source manual, status failed, the same `error_category` `run_pipeline` records for that fault, outcomes from the ledger), and the exception reaching `on_error` is the same object · a config that fails to parse records `config` on BOTH entry points · a successful Convert writes exactly one success record; the four non-results write none · a store write that raises during failure recording never masks the original · Home from [older success, newer failed manual attempt] → per **D14** (the AC is written once D14 is decided; default (a) → FAILED_ETL) · a delivery record after a partial build leaves Home PARTIAL · signature pin · canonical DoD.

**Tests:** monkeypatched Staff raising `SourceSchemaError` → one failed record, category `source_schema`, `excinfo.value is injected` (twin: clean → one success record) · YAMLError config → one CONFIG record · `write_run_record` patched to raise → original propagates (twin: healthy store records) · unset output folder → zero records · parity for parametrised injected faults (SourceSchemaError in a CRITICAL entity, ExtractionError, YAMLError) · `inspect` shows KEYWORD_ONLY, no default · Home ordering twins.

**Docs:** `failure-policy.md` §7 symmetric-sink row → ENFORCED · ROADMAP (close crashed-Convert item) · `DECISIONS` (the enumerated non-records) · `CHANGELOG`: "A failed Convert now appears in Run History".

---

### S6 — Own-file missing-mapped-column observation (advisory; preflight runs at run time) [M] · deps: S4

**Files:** `src/etl/preflight.py` (NEW pure `missing_columns_by_entity(config, input_columns)`; extend `ExpectedColumn` with a `guard`/role field rather than a parallel list) · `src/etl/outcomes.py` (`EntityOutcome.missing_mapped`; reason refinement helper) · `src/etl/pipeline.py` (ONE shared `observe_source_columns(config, raw_data, ledger)` called after `load_data`) · `src/ui_flet/screens/convert.py` (same call) · `src/ui_flet/failure_copy.py` (sentence for EMPTY refined to MISSING_SOURCE_COLUMN) · tests: `test_etl_preflight.py`, `test_pipeline_run_store.py` · docs.

**Design.** Own-file comparison: for each ACTIVE entity, compare its field_map + row_filters source columns with the normalised headers of THAT entity's OWN `source_files` only (`_observed_names` :411-430 merges every file — the masking bug). EXCLUDE `source_columns` (documented cross-file auxiliary reads). Keep the soundness rule (no claim unless ALL that entity's files were observed with headers). Headerless files compare against declared headers (always match — correct). The implementer verifies transformer-by-transformer that each field_map reads only frames from the entity's declared sources; an entity that merges a foreign file falls back to the all-files comparison (documented). `observe_source_columns`: logs ONE WARNING per entity naming missing mapped columns in CONFIG spelling; attaches `missing_mapped` to the ledger; wrapped in its own `except Exception` at DEBUG so it can NEVER change a run result (P11). Reason refinement: EMPTY/NO_ROWS_AFTER_TRANSFORM + missing mapped → EMPTY/MISSING_SOURCE_COLUMN. **Measurement is measure-and-record only:** run the sweep over every bundled contract fixture AND every real local drop; record findings in DECISIONS per district/file/date; **never change a shipped config in this slice** (a `{value: ""}` "fix" would permanently blank Family Email when Unity's Enhanced report returns — the refuted remediation); a real finding is an owner/partner action.

**AC:** SD67-shaped fixture (Family maps `Email Address`; file lacks it) → Family EMPTY/MISSING_SOURCE_COLUMN, `missing_mapped == ("Email Address",)`, one WARNING · a column present in ANOTHER entity's file does not hide a miss in this entity's own file · no claim for an entity whose files were not all observed · helper raising internally → byte-identical run result · sweep results recorded in DECISIONS; no config edited; SD74 unchanged · canonical DoD.

**Tests:** own-file twins; soundness twins; `source_columns` exclusion; total-by-contract over garbage input; the parametrised sweep over `available_configs()` fixtures (zero findings, or each finding listed — twin: a doctored fixture dropping one mapped column gives exactly one); refined reason + `missing_mapped` recorded; raise-isolation twin; AST pin that both entry points call `observe_source_columns`.

**Docs:** `failure-policy.md` §5(d)/§10 → ENFORCED · `INVARIANTS`: "source observation never enforces; guards are enforced once, where used" · ROADMAP (SD67 diagnostic half closed) · `DECISIONS` (measurement) · `CHANGELOG`.

---

### S7 — (Owner-gated D4) Config-declared file and column labels in copy and record [S] · deps: S6

**Design.** `outcomes.safe_label(text, *, vocabulary)`: membership-validated against the RESOLVED config's own vocabulary (filenames from `source_files`; columns from field_map/row_filters in CONFIG spelling — never lowercased), printable, ≤120 chars, no newline; anything else dropped. **Name a file ONLY when the entity has exactly one configured source file, or the raising error itself carries the `source_files` role**; otherwise column only. `EntityOutcome` gains `labels: tuple[str, ...] = ()`; `outcome_sentence` gains a label-aware variant (twin: no labels → S3's sentence). Amend `home_status.py:15-26`'s privacy rule to "config-declared file and column labels only, validated; never observed header text, `str(e)`, paths or cell values". Build on `preflight.missing_columns_by_entity` (S6) — no separate membership check.

**AC:** the Unity plain-report record and Home copy name `EmergencyContactInformation.txt` and `Parent Auth / Guardian` in config spelling · a label not in the config vocabulary (simulated observed header), with a newline, or >120 chars is never stored or rendered · exception text never reaches copy or store (sentinel) · a 5-source-file entity (Classes) never names a file · canonical DoD.

**Tests:** `safe_label` twins; membership twins; single-file rule twins; round trip; `tests/test_identity_pii_guards.py`: no email-shaped or path-shaped string reaches the record via labels.

**Docs:** `failure-policy.md` §8 → ENFORCED (labels) · `DECISIONS` (D4 ruling + amended rule) · `CHANGELOG`.

---

### S8 — (Owner-gated D5) Standing warning while an enabled entity produces nothing [S] · deps: S4, S6

**Design.** `failure_copy.OUTCOME_TIER: (entity, kind, reason) -> Verdict` — a function of entity class AND cause, not cause alone: WARNING for EMPTY/NO_ROWS_AFTER_TRANSFORM and EMPTY/MISSING_SOURCE_COLUMN on any entity; EMPTY/SOURCE_FILES_EMPTY and NO_SOURCE_FILES_DECLARED neutral ONLY for entities in a single-sourced `MAY_BE_EMPTY` set beside `ROSTER_ANCHOR_ENTITY` (start with `{StudentAttendance}`; completeness test), WARNING otherwise; FAILED always WARNING (S3). `classify_latest_reason`'s PARTIAL condition becomes "any outcome whose tier is WARNING". The vanished-entity anomaly and this standing warning compose by precedence (PARTIAL outranks ANOMALY); the anomaly leg is left as-is. No ETL change. **Measure first** over every bundled fixture and real local drop; list in DECISIONS every district whose Home would turn amber, with owner approval; pair any SD51 amber with a vendor review of `sd51myedbc`'s Family mapping. Remedy is a config change, never muting.

**AC:** SD51-shaped fixture (Family, zero emails) PARTIAL on night 1 AND night 2; twin: emails present → CLEAN · `MAY_BE_EMPTY` entity with SOURCE_FILES_EMPTY leaves the verdict unchanged; a non-member with the same reason → WARNING · DECISIONS lists affected districts with approval · SD74 unchanged · canonical DoD.

**Tests:** two-night persistence twins; `OUTCOME_TIER` totality over valid triples; `MAY_BE_EMPTY` completeness; Home/Run History parity.

---

### S9 — One column resolver; finish the `context.py` migration; delete the conftest mask [M] · deps: S1 (base.py order: after S1, before S10)

**Files:** `src/etl/transformers/columns.py` (NEW, pandas-free) · `base.py` (`resolve_column` :511-527 DELETED; callers migrated) · `family.py` (`_student_number_col` :78-88 DELETED) · `student_courses.py` (`_field_map_source` :168-175 DELETED) · `students.py:193` (the Grade column resolved for the student grade scope *(R12)*) · `classes.py` (:155; `_assign_grades` :437-446 inline resolution) · `enrollments.py` (:116, :312) · `context.py` (`get_students_config` :148-149 → `(self.entity_mappings or self.global_config.get("mappings", {})).get("Students", {})` mirroring 0052's `get_teacher_id_col`; `get_demo_student_col` :151-163 via the resolver) · `tests/conftest.py` (delete the nested `mappings` injection at :327; fix fixtures to the production shape via `set_entity_mappings`, never re-mask; list them in the PR) · `tests/test_column_resolution.py`, `tests/test_column_resolution_pin.py` (NEW) · docs.

**Design.** `resolve_source_column(field_map, key, *, default) -> str` with ONE policy built on the typed layer that already exists (`models.classify_field`/`ensure_field_mapping` — do not invent a second raw-dict rule): dict → non-blank `column`; non-blank bare str → itself; `{value: …}`/ID-role/email-format shapes → the documented default (explicit allowlist, not a silent catch-all); always `column_names.normalize_column_name`. A fallback to the default logs ONCE per run at DEBUG (a permissive default must say so). **Transitional runtime WARNING** *(R12, re-reviewed)*: deployed user-folder overlays live on district servers where no local scan reaches, so for ONE release the resolver logs a WARNING (once per run per key, config vocabulary only) whenever its answer differs from what the old `resolve_column` would have returned — the district's log then shows any rename that just started taking effect; removed the release after, via ROADMAP. Delete the three old resolvers and every ad-hoc `.get("Homeroom","homeroom").lower()`; no wrappers. **Measurement gate BEFORE merging:** a scratchpad script lists every key read through the old `resolve_column` whose value is a bare string differing from the old default — over every bundled config AND every user-folder overlay found locally under `paths.user_mappings_dir()` and the partner-drop folders *(R12)*; run SD74 snapshot, all contract fixtures, `test_pipeline_e2e_districts.py`; **if ANY CSV changes, STOP and report the per-config diff to the owner (D8)** — never update goldens silently. Expected diff: none (base's `"Homeroom": "Homeroom"` normalises to the default). AST pin over `src/etl/transformers`: no `.get(<str>, <str>)` whose result is `.lower()`-ed; no subscript/`.get` of literal `"column"` on a field_map entry outside `columns.py` (allowlist with reasons for the `apply_field_map` engine).

**AC:** exactly one resolver; the three old ones deleted (grep) · a Students field_map `"User ID": {column: "Pupil No"}` now drives the homeroom active-roster filter in Classes and Enrollments (before: silently fell back to `student number` and `filter_to_active` skipped) · conftest no longer injects `mappings` · AST pin green, twin catches a synthetic violation · SD74 + contract suite unchanged, or an owner-approved diff list in DECISIONS · canonical DoD.

**Tests:** parametrised shapes (dict with column / without / blank column / bare string / blank string / None / absent key / non-string / value-shape / id-role); normalisation; context accessors honour `entity_mappings` (twin: empty → defaults); integration: renamed User ID honoured in homeroom filtering (twin: defaults ≡ golden); the AST sweep + violating-snippet twin.

**Docs:** `ARCHITECTURE_TREE` (columns.py; context/base lines) · `failure-policy.md` §9 → ENFORCED · CLAUDE.md "Configurable Columns": one sentence pointing at `columns.resolve_source_column`, in place, plus two in-place edits *(#12)*: CLAUDE.md :176 (the `student_rostering_grades` paragraph — its "Grade column via `resolve_column`" becomes `columns.resolve_source_column`) and :236 (the `record.get("final mark")` example line) · ROADMAP (close T1 rename + divergent-resolvers) · `DECISIONS` (survey result).

---

### S10 — Fail-closed conformance for guard classes (a)/(b): `require_columns`, no raw KeyError, no partial ship [M] · deps: S4, S9

**Files:** `columns.py` (`require_columns`) · `base.py` (`apply_row_filters` via the helper) · `students.py` (grade-scope, cross-enrollment, derived_dates via the helper) · `classes.py` (`_merge_course_and_staff` :382-392 join columns AND :398-402 `staff_df[[teacher_id_col, LAST_NAME]]`; the homeroom column guard placed WHERE THE COLUMN IS READ, after ~:152 — not on config state) · `enrollments.py` (require merge columns up front after ~:121; DELETE the `except (KeyError, pd.errors.MergeError)` at :159-161) · `tests/test_require_columns.py` (NEW) + per-site tests · `test_failure_policy_parity.py` · docs.

**Design.** `require_columns(available, required, *, entity, guard: GuardKind) -> None`: normalises both sides, collects EVERY missing column (config spelling), raises ONE `SourceSchemaError`; message/log carry missing names + available COUNT + a `header_looks_like_data` flag — never observed headers. Each site tagged `# failure-policy: pii_scope|join_key`. Enrollments' homeroom merge: a `MergeError` now propagates; the entity is CRITICAL so the run fails with the entity named and the last good output untouched — no partial `Enrollments.csv` that would unenrol every omitted student (faq :113). **Verify first** whether :159-161 is reachable (see verify list). **Measurement gate:** run all contract fixtures, SD74, `test_pipeline_e2e_districts.py` and every real local drop; confirm NO bundled config reaches a newly fail-closed path; STOP and escalate (D9) if one does. Parity: §5 site catalogue ↔ `require_columns` call sites collected by AST (entity + guard), both directions.

**AC:** no missing-column fault in classes (a)/(b) reaches `run_pipeline` as `KeyError`/`AttributeError`/untyped `ValueError`; each arrives as `SourceSchemaError` with entity, columns, guard · the enrollments partial ship is gone · a guard failure in a CRITICAL entity fails the run with `source_schema` and the entity named; in an ISOLATABLE entity → FAILED/MISSING_SOURCE_COLUMN · the PR documents the measurement: zero bundled-config behaviour change or an owner-approved exception · §5 ↔ call-site parity green · changed messages that break `match=` tests are listed explicitly · canonical DoD.

**Tests:** all-missing-listed-at-once; case/whitespace-insensitive; twin: all present → no raise; guard + entity carried · per-site pairs (e.g. course info without `SCHOOL_NUMBER` → JOIN_KEY error; twin: present → built; staff frame without `LAST_NAME` → error; enrollments homeroom merge column missing → raises, no partial frame; cross-enrollment collapse without home-school column → raises; twin: collapse off → no check) · parity with doctored-catalogue twin.

**Docs:** `failure-policy.md` §5(a)/(b) → ENFORCED · ROADMAP (close partial-ship + classes raw-KeyError) · `DECISIONS` ("partial homeroom ship removed; join-key absence fails the entity", citing faq :113) · `CHANGELOG`.

---

### S11 — Fail-open conformance: every (d)/(e) posture records a note, none is silent [M] · deps: S10, S3

**Files:** `outcomes.py` (`OutcomeNote(StrEnum)`; `EntityOutcome.notes: tuple[tuple[OutcomeNote, int], ...] = ()`) · `context.py` (`record_outcome_note(entity, note, count)` — a METHOD on `TransformContext` writing into an existing `notes` list initialised in the context's own dataclass; transformers CALL it and never assign a new context attribute, so S2's publisher AST pin stays true *(#20)*; one `BaseTransformer.record_outcome_note` helper deduping per (note, entity) per run) · `base.py` (:288-294 fallback; :295-301 all-Active default; `resolve_active_config` :184-233 configured-status-column-absent; `filter_to_active` :366-368 — separate "roster empty" from "column unresolvable") · `staff.py` (:273-275; `_merge_roster` skip; :281-299 warnings also recorded) · `enrollments.py` (:290-293) · `blended.py` (:551, :591 made consistent with :654) · `family.py` (no-email exclusion count; missing Email output column) · `failure_copy.py` (note copy) · `run_history.py` (row detail) · tests · docs.

**Design.** Closed `OutcomeNote` members, each with a §5 row, a tag and copy: `STATUS_COLUMN_ABSENT_DATE_ONLY` (once per run — the mode taken), `CONFIGURED_STATUS_COLUMN_ABSENT`, `ACTIVE_WITHOUT_POSITIVE_SIGNAL` (count of rows Active because BOTH status and withdraw date are blank), `ALL_ACTIVE_DEFAULT`, `STAFF_STATUS_COLUMN_ABSENT`, `STAFF_STATUS_VOCABULARY_UNRECOGNISED`, `STAFF_FILTER_WOULD_EMPTY`, `ROSTER_MERGE_SKIPPED`, `ACTIVE_ROSTER_COLUMN_UNRESOLVABLE`, `COTEACHER_SOURCE_UNUSABLE`, `BLENDED_LOOKUP_COLUMN_ABSENT`, `CONTACTS_EXCLUDED_NO_EMAIL`, `EMAIL_OUTPUT_NOT_MAPPED`. Precedence for the student-status branch: CONFIGURED_STATUS_COLUMN_ABSENT > STATUS_COLUMN_ABSENT_DATE_ONLY > ALL_ACTIVE_DEFAULT. Rules: at most ONE aggregated log line per site per run (the blended many-warnings trap near :370); counts and vocabulary only; **NO direction changes** (a direction change is its own owner decision/slice — D10); notes appear in Run History row detail only, EXCEPT `ALL_ACTIVE_DEFAULT` = WARNING tier (H1). **Honesty note:** Unity's 20 alumni (no status column, blank withdraw date, DOB 1995-96) are NOT distinguishable from current students by any of these signals — they surface as part of `STATUS_COLUMN_ABSENT_DATE_ONLY`'s existence, not as a count; the alumni themselves remain a partner data gap. Parity: extend to a full bijection over every `# failure-policy:` tag under `src/etl`.

**AC:** every §5 (d)/(e) site emits exactly one aggregated line + one note when triggered, neither when not · delivered bytes unchanged for every fixture · Unity-shaped demographic export records `STATUS_COLUMN_ABSENT_DATE_ONLY` · sentinel names/emails in no new log line or note · tag ↔ catalogue bijection green · canonical DoD.

**Docs:** `failure-policy.md` §5(d)/(e) → ENFORCED; catalogue complete · ROADMAP (all-Active default narrowed pending D10) · `DECISIONS` · `CHANGELOG`.

---

### S12 — Config typo visibility (origin-keyed) [S] · deps: S1

**Files:** `src/config/loader.py` (ONE pure walker `unknown_config_keys(resolved_raw, model) -> list[UnknownKey(location, key, suggestion)]` run in the shared validate path — no `model_validator(mode="before")` private-attribute trick, which cannot be built in pydantic v2) · `src/config/models.py` (`FieldTransform`, `FieldNameConfig` → `extra="forbid"`; `classify_field`'s fallback raises naming field + nearest key via difflib instead of warn-and-return-raw *(verify current behaviour at ~:180/:194-195)*) · `src/config/authoring.py` (`write_overlay`/`validate_overlay` refuse) · `tests/test_config_unknown_keys.py` (NEW) · docs.

**Design (origin-keyed, matching `_apply_user_dir_domains_floor` :358/:447's direction — never invert):** BUNDLED configs: an unknown `GlobalConfig`/`EntityConfig` key RAISES at load (caught by `make validate-config`/CI, so it cannot ship). USER-DIR overlays: WARN at run time (one line per key: location, key, nearest known key), so a deployed nightly is never broken by a stray key; REFUSE at authoring. Field-mapping leaf models (`FieldTransform`/`FieldNameConfig`): `extra="forbid"` for ALL origins — a `transfrom:` typo silently ships untransformed data, which is wrong output, not a harmless stray key (D11 confirms). Root `MappingConfig` stays `extra="ignore"` (forward compat). No `version:` bump (`SUPPORTED_CONFIG_MINOR` stays 13). Dead keys found in bundled configs are removed after confirming they are unread (DECISIONS).

**AC:** all bundled configs load with ZERO unknown-key findings · `enabled_entites` raises for a bundled config, warns + suggests for a user-dir overlay; `transfrom:` refuses everywhere with the suggestion · `validate_overlay` refuses either · a root-level unknown key is still ignored (forward-compat twin) · declared-range and version-gate tests unchanged · the input dict is not mutated · canonical DoD.

**Docs:** `adding-district.md` · `failure-policy.md` §6 config row → ENFORCED · CLAUDE.md (edit the "typo'd `enabled_entities` is silently dropped" clause in place) · ROADMAP (close `transfrom:` T2) · `DECISIONS` · `CHANGELOG`.

---

### S13a — Architecture fitness functions, justified broad excepts, one config-count constant [S] · deps: S10, S11, S12
### S13b — The schema-drift matrix [M] · deps: S13a *(split per the Stage 3 sizing finding: the matrix runs the pipeline for every config on three OSes and needs its own CI time budget; measure its runtime and mark it `@pytest.mark.slow`-style if it exceeds the suite's norm)*

**Design (S13a unless marked).** `tests/test_architecture_fitness.py` (AST, no new dependency), rules: (a) `src/etl`, `src/config`, `src/history`, `src/quality` never import `flet`/`src.ui_flet`; (b) an EXPLICIT `FLET_FREE_MODULES` list (`errors.py`, `outcomes.py`, `preflight.py`, `columns.py`, `failure_copy.py`, `home_status`, `convert_result`, `run_history`, `setup_gates`, `setup_flow`, `schedule_status`, `schedule_probe`, `identity_gate`, `config_editor`, `mapping_catalog`, `humanize`, `verdict`, `nav`, `tokens` — NOT `theme.py`, which imports flet at :16, nor `job_runner.py`) never import flet; (c) `src/etl/transformers` never imports `src.etl.pipeline`; (d) the consolidated one-boundary pin; (e) classifier bodies contain no `str(`/`in <str>`. Each rule has a detector self-test on synthetic violating source; each rule's target list is a declared registry that fails loudly if a path is missing. **ruff `BLE`** selected with per-file-ignores outside `src/etl`, `src/config`, `src/history`, `src/quality`; every in-scope `except Exception` gets `# noqa: BLE001 — <reason>` from a closed reason vocabulary — **annotate, never narrow in this slice** (`loader.py:323` `_commit_staged`'s rollback must stay broad); regex test requires reason text. **Config count:** `BUNDLED_CONFIG_COUNT = 20` in `tests/_pins.py` (importable, not conftest); replace every literal in `tests/test_ui_flet_filtered_pickers.py` (≥9 sites), `tests/test_ui_flet_mapping_catalog.py:704/984`; `tests/test_config_version_gate.py` derives `ALL_BUNDLED_CONFIGS` from `available_configs()` and asserts `len == BUNDLED_CONFIG_COUNT` (fixes the missing unitychristian); `.github/workflows/ci.yml:65` keeps its literal in documented lockstep with a parity test that reads the YAML. **Schema-drift matrix (S13b):** parametrised over every bundled config × active entity × guarded column (from `preflight.expected_columns` with guard roles): drop the column from the contract fixture, assert the declared outcome (entity FAILED/MISSING_SOURCE_COLUMN if ISOLATABLE, run fails with `source_schema` if CRITICAL) and other entities' outputs byte-identical. One hypothesis property for `apply_field_map` totality (never raises; output rows == input rows). CANDIDATES.md stages "fitness functions for layering" and "level-triggered absence".

**AC:** every rule green on the real tree; each detector self-test flags its synthetic violation · every in-scope broad except annotated; `ruff check` green · a grep-based test finds no numeric config-count literal in tests/ outside `_pins.py` (allowlist); monkeypatching `available_configs` to 19 fails the pin · the drift matrix passes for all 20 configs · canonical DoD.

---

### S14 — (Partner-gated) Record SpacesEDU's Q5 answer; apply the promotion rule [S per promotion] · deps: S4 + the answer

Record the answer verbatim in DECISIONS and as dated confirmation rows in `output-contract.md` (existing format); flip `Q5-status: answered`, which releases the FAQ's pending clause via S4's parity test. Then, ONE ENTITY PER CHANGE: if the answer says an absent `Family.csv` unlinks guardians, the owner decides whether Family becomes CRITICAL (one table line; parity forces the doc). If the answer shows an absent CourseInfo/StudentCourses/StudentAttendance (or a StudentCourses without its CourseInfo, Q5d) harms imported records, the owner decides whether to DEMOTE that entity to CRITICAL (or declare the pair's withholding), each in its own change with a contract fixture and a DECISIONS entry. If the answer shows an EMPTY critical entity's absence deactivates users, open a NEW plan for D6 — not this slice.

---

### S15 — (Owner-gated D13) CLAUDE.md becomes rules + index; subsystem narratives move to developer guides [M] · deps: S13, lands LAST

**Design.** Assign EVERY CLAUDE.md heading a disposition in an exhaustive map: KEEP-RULE (any prohibition/safety/privacy/gate/contract stays as a one-liner — incl. Security, tool-output-is-DATA, Testing Conventions, land gate/DoD, exit codes, the harness-managed block byte-identical) / POINT-TO-EXISTING / MOVE VERBATIM with a one-line pointer left behind (plan-0049 machine-scope narrative → `docs/developer/machine-scope.md`; Desktop UI screen notes → `ui-surfaces.md`; self-service districts → `self-service-mappings.md`). First grep every test that reads CLAUDE.md (`test_creator_doc_copy_parity.py`, `test_partner_doc_schedule_copy_parity.py`, `test_ui_flet_band_copy_parity.py`, …); these check BOTH directions over a closed list (undeclared quotes must be ABSENT), so each `_DOC_QUOTES` table registers the new guide and each repointed test is shown red-first.

**AC:** every removed block exists verbatim in a linked guide (reviewer diff-checks) · every doc-parity test green after repointing, each proven red-first · the owner approves the new CLAUDE.md before merge.

---

## Owner decisions (Gate A = D1–D4 before S1; Gate B = D4/D5 before S7/S8; D13 before S15)

| # | Question | Options | Recommendation |
|---|---|---|---|
| **D1** ✅ **DECIDED 2026-09-23: (c)** | Which entities are ISOLATABLE before SpacesEDU answers Q5? | (a) Family only · (b) Family + StudentAttendance · (c) Family + CourseInfo + StudentCourses + StudentAttendance · (d) none | **Owner chose (c).** Orchestrator had recommended (b): Family: incident-proven flip; absence already ships (SD51; pinned :581-590); faq :109/:113 don't list it; publishes no context state. StudentAttendance: an unmapped absence code raises (`student_attendance.py:349-350`) and kills rostering — exactly what `output-contract.md:558` ("a missing attendance drop must never block rostering") forbids; publishes no state; nothing reads it; absence already ships on nights without absence files. Counter-evidence: the contract's Delivery envelope says SpacesEDU's nightly check looks for `StudentAttendance.csv` by name — an alert on THEIR side is the right kind of loud. CourseInfo/StudentCourses stay CRITICAL: unknown whether they must arrive together (Q5d) and no absence evidence. You accept explicitly: Unity's guardians linked since 2026-09-14 may be unlinked by a delivery without `Family.csv` (unknown; recoverable next good night). (d) keeps a certain harm to avoid an uncertain one. |
| **D2** | Send Q5 to SpacesEDU now? | send now / wait | **Send now** (owner action; neutral wording from S0). Highest priority: Q5e (guardian unlink) and Q5c for StudentAttendance. The SD51 empirical check (no `Family.csv` since 2026-09-21 — did anything get unlinked?) can be asked at the same time. |
| **D3** ✅ **DECIDED: (a)** | Exit code for a nightly that delivered everything except an isolatable file | (a) 0 + PARTIAL record · (b) new code 4 | **(a)** — documented "partial run stays exit 0" contract; the warning repeats nightly on Home. |
| **D4** ✅ **DECIDED: (a)** | May record + on-screen copy name the file/column as CONFIGURED? | (a) yes, validated + sanitised (S7) · (b) entity + fixed reason only | **(a).** Config vocabulary is not student data; without it the admin cannot act (support had to ask SD67 for a header row on 2026-09-22). Requires amending `home_status.py:22-26`; observed header text stays banned. |
| **D5** | Standing amber while an enabled entity produces nothing? | (a) amber when every row filtered/column missing; neutral when a `MAY_BE_EMPTY` entity's source is empty (S8) · (b) always · (c) never | **(a)**, after measuring which districts turn amber; pair SD51's amber with a vendor review of its Family mapping. |
| **D6** | Today an EMPTY Staff/Classes/Enrollments ships as an absent file and stays green (pinned at `tests/test_pipeline_delivery_integrity.py:592-601`). Refuse delivery? | (a) keep until Q5, S8 makes it amber · (b) refuse now · (c) never | **(a)** — same unknown as D1 in the deactivation direction; refusing now reverses a pinned design without evidence. Interim: S8's WARNING. |
| **D7** | Unity after S4 ships | (a) keep Family enabled; ask the school for the Enhanced report (partner action) · (b) disable Family until then | **(a)** — the plain report then drops only Family with a nightly warning; the Enhanced report restores it with no release. The 2026-09-23 CLI overlay was local-only and is already gone. |
| **D8** | If S9's resolver honours bare strings and changes a bundled district's CSVs | (a) accept as a bug fix after seeing the exact diff; update goldens + CHANGELOG · (b) keep old behaviour | **(a)**; the implementer STOPS and reports, never updates goldens silently. Expected diff: none. |
| **D9** | If S10's measurement finds a bundled district shipping a partial homeroom Enrollments set or silently skipping homeroom classes | (a) fail with a named reason · (b) warn and keep shipping | **(a)** — a silently shrunken `Enrollments.csv` unenrols students (faq :113). Expected impact: none pending measurement. |
| **D10** | A student export with neither status nor withdraw-date column ships everyone Active (`base.py:295-301`) | (a) after S11 surfaces it, fail Students with a named reason · (b) keep shipping + amber | **(a)** as the target, switched only after S11's measurement confirms no bundled config/real drop hits this path. Unity's alumni are NOT this case. |
| **D11** | Unknown-key severity | (a) bundled RAISE · user-dir WARN at run / REFUSE at authoring · `Field*` leaf models forbid everywhere (S12) · (b) warn-only everywhere | **(a)** — matches the existing origin-keyed direction; a `transfrom:` typo is wrong output, not a stray key. |
| **D12** | In-app entity on/off toggle? | (a) no · (b) later, overlay-gated, ISOLATABLE only | **(a)**. |
| **D13** | Approve the CLAUDE.md restructure (S15)? | approve after S13 / defer | **Approve after S13** — the always-loaded file currently buries the rules under scheduler/UI detail; S0's one-line pointer makes it an improvement, not a prerequisite. |
| **D14** *(R, recommended #1)* | Once S5 records failed manual Converts: should a failed manual attempt AFTER a good nightly turn Home's verdict to "Last sync failed"? | (a) yes — newest record wins, as it does today for a manual SUCCESS after a failed nightly · (b) Home's verdict keys on the newest record whose source is `scheduled`/`cli`; manual failures show in Run History only | **(a)** for symmetry and honesty (the last thing that happened failed); Run History already labels the source. If the owner picks (b), S5's Home AC and `classify_latest_reason` gain a source filter — one slice, no other change. |

Resolved without an owner decision (design, not policy): Convert needs no NEW confirmation before a partial write — the existing anomaly-ack gate already fires for a failed entity with a previous CSV, and its prompt gains the reason (S4).

---

## Review  _(filled by plan-reviewer, Stage 3)_
- **Verdict:** **CHANGES REQUIRED** _(plan-reviewer, 2026-09-23, read-only against `8d33664`)._ The spine is sound. It puts the bulkhead at the one seam (`pipeline.py:345`), makes criticality restrictive by default, types errors, adds `entity_outcomes` as an additive key with no DDL, and lands the reader before the producer. About 60 `file:line` citations were spot-checked and all are accurate to ±2 lines except those corrected below. **Q1 holds on the CLI path.** `apply_row_filters` raises (`base.py:483-488`), the S4 bulkhead catches it, and Family never enters `outputs` (`pipeline.py:351` is skipped). `check_delivery_integrity` passes because Students is present (`:459-468`). The vanished-Family anomaly logs (`:532-543`); it does not block. `save_all` then writes 4 files and `archive_stale_outputs` moves the old `Family.csv` into `archive_<ts>/` (`:1010`, `loader.py:463-516`). The manifest is `output_filenames(outputs)` (`:1026`), so no non-guardian row can ship. The record is success with Family FAILED, and Home shows PARTIAL. **Convert** asks for anomaly consent while a previous `Family.csv` exists (`convert.py:344-351`), and the creator gate goes FAILED as specified. **However**, one step can make a district worse (#1), two ACs cannot pass as written (#3, #10), and several slices would leave an implementer guessing.
- **Required changes:**
  - **BLOCKERS: fix before Gate A / S1.**
  - **1. S4 Design 4 (`deliver_job` manifest = files present ∩ newest record's BUILT): unsafe, and it breaks FIX-4.**
    - `read_run_records` returns every district's records mixed, newest first, and records carry no output folder (`store.py:245-260`).
    - After a CRITICAL raise, the newest record (from the CLI failure sink, or a Convert record after S5) shows earlier entities BUILT and the rest NOT_RUN. Nothing was written, so the previous full set is still intact on disk. This rule would ship `Students.csv` alone, i.e. Staff/Enrollments absent *because of a fault*. That violates the plan's own H2.
    - A newest record from another district, or a `delivery_only` record (`entity_outcomes=None`), leaves the rule undefined.
    - Changing only `deliver_job` splits the ONE derivation the readiness gate, vintage line and payload share (`convert_output.py:16-29`, `deliverable_files` :549-580, `deliverable_manifest` :583-600).
    - **Fix: drop S4.4.** A failed entity is already absent from `outputs`, and the same run archives its CSV out of the top-level set. The residual (the best-effort archive fails: `loader.py:507-515`) is pre-existing, applies to every vanished entity, and belongs on the ROADMAP.
    - If S4.4 is kept, it must:
      - live inside `deliverable_files`;
      - key on the newest *success, non-delivery-only, same-`sis_type`* record;
      - only EXCLUDE FAILED/EMPTY entities, never restrict to BUILT;
      - add `convert_output.py` and its tests to Files.
  - **2. The P-rules are undefined (S0 §1/§7–§11, S0 Design 5, Approach).**
    - The plan cites P1, P3, P7, P8, P9, P10, P11, P12 and P16 but defines only P1. P2, P4–P6 and P13–P15 are never mentioned.
    - S0 must still "add one INVARIANTS row per P-rule" and key §7–§11 to them, so the S0 implementer would have to invent the rules.
    - Fix: add a numbered P-list to Approach (one line each: the rule, its §, its enforcing slice), or drop the numbering and key everything to § numbers.
  - **3. S1's AC "every existing test passes unmodified" cannot pass.**
    - `tests/test_student_rostering_grades.py:163` and `:287` pin `pytest.raises(KeyError)` for `grades.filter_to_grade_scope`. That KeyError contract is documented at `grades.py:329` and `:387-389`.
    - `:163-167` also ASSERTS that the message names the available columns (`"student number" in message`). That is exactly the header dump S1 deletes.
    - `SourceSchemaError(EtlError, ValueError)` is not a `KeyError`.
    - Fix:
      - list both tests as deliberately changed: assert `SourceSchemaError`, the missing CONFIG name and the column COUNT, and the ABSENCE of a sentinel observed column;
      - update grades.py's docstring contract;
      - keep the `not found` wording that `tests/test_transform_base.py:171` matches.
  - **4. S3/S5: a config-load failure gets one category on the card and another in the record.**
    - `classify_error_category`, by type alone, maps the loader's pydantic/version-gate `ValueError` to DATA and `yaml.YAMLError` to UNKNOWN.
    - The CLI records CONFIG *at the call site* (`pipeline.py:856-881`, whose comment explains the category is only knowable there), and S5 does the same in Convert.
    - So `error_card_copy(exc)` in `_on_error` (`convert.py:818-831`) would render DATA/UNKNOWN copy for a fault the record calls CONFIG. That fails S3's own AC "FAILED copy per category is identical".
    - Fix, either:
      - S5 wraps the error as `ConfigLoadError(EtlError)` with category CONFIG via `raise … from exc`. Restate S5's "same object reaches on_error" AC as "the wrapped object, cause preserved", and keep `config_editor.humanize_config_error` reading the cause.
      - Or `convert_job` returns a CONFIG `ConvertResult` instead of raising.
  - **5. S4 Design 5 makes the packed-exe smoke vacuous.**
    - `scripts/ci_flet_pack_smoke.py:897,903` passes on `name in proc.stdout` for each rostering entity. A `  Family: NOT BUILT — …` line satisfies it for an entity that was never built.
    - Fix:
      - add the script to S4 Files;
      - match the built-row shape (`"  {name}: <n> rows"`), or word the NOT-BUILT line without the bare entity token;
      - add a negative twin;
      - keep the `=== DRY RUN` banner byte-identical.
  - **REQUIRED: fix in the plan before Stage 4.**
  - **6. S2's Files list is incomplete.**
    - Making `run_transform(*, ledger)` required and `TransformOutputs` 5 fields long breaks the shared fixture `tests/conftest.py:760` and `tests/test_pipeline_required_input.py:324/341/382`. It also breaks the 4-tuple unpacks at `tests/test_main_helpers.py:631,:700` (that file is listed).
    - Add the two files. S9 also edits `conftest.py`, so add it to the conflict map.
  - **7. S2: two boundary rules are undefined or false.**
    - (a) "`None` when the attempt ended before transform" contradicts "a pre-loop raise → every entity NOT_RUN/RUN_ABORTED". It is unclear which applies to `ExtractionError`/`NoUsableInputError` (`pipeline.py:935-949`, after the ledger can exist), and S5's CLI≡Convert parity over `ExtractionError` needs one answer. Fix: `None` iff the config never loaded (no ledger was constructed); every later raise → `finalize_aborted()`.
    - (b) "flat counts … 0 on a failed run" is false. The failure sink uses `_counts_from_outputs(outputs)` (`pipeline.py:1102`), and Convert's integrity refusal records the produced counts (`convert.py:329`). Correct the docstring spec.
  - **8. S3 has an import cycle.**
    - `ENTITY_LABELS`/`SIZE_NOUNS` live in `home_status.py:79/:115`. `failure_copy` importing them while `home_status` imports `failure_copy` is a cycle.
    - Fix: in S3, move both maps to `humanize.py`, a leaf that `home_status` already imports (`:52`). Record the move in the Naming table.
  - **9. S3 level-trigger gap.**
    - A deliver-from-disk after a PARTIAL build paints Home green: `classify_latest_reason(records[0])` (`home_status.py:819`) classifies a `delivery_only` success as CLEAN.
    - Fix: for a delivery-only latest record, derive PARTIAL from the newest BUILD record, using the walk-back the counts already do (`home_status.py:622-625`). Add a twin.
  - **10. S2 `DEPENDS_ON` / S4 Design 1: the dependent-withholding branch is dead by construction.**
    - S2's test forbids an ISOLATABLE entity in any `DEPENDS_ON` value, and a FAILED CRITICAL entity re-raises. So S4's test "a dependent of a FAILED entity is NOT_RUN and the run fails" can only be built by monkeypatching the table.
    - `StudentCourses→{CourseInfo}` is not a code dependency either: `student_courses.py:112-114` reads the raw `CourseInformation.txt`.
    - Fix:
      - define `DEPENDS_ON` as *delivery-referential*;
      - keep the S2 invariant as THE mechanism;
      - delete the S4 branch and its test;
      - give S14 any withholding logic, for when a depended-upon entity is first promoted.
  - **11. S4: an isolated failure that leaves `outputs` empty is recorded as NO_OUTPUT.**
    - On `sd51attendance`/`mbponly`-shaped configs, a StudentAttendance vocabulary raise (`student_attendance.py:349-353`) records `data` today.
    - After S4 it records `no_output`, whose message (`pipeline.py:452-456`) says "every entity was empty or skipped … check the input folder". That is a worse diagnosis.
    - Fix: when no entity is BUILT and at least one FAILED, re-raise the first FAILED exception (bare) instead of falling through to the gate. Drop the "accepted" risk line.
  - **12. S9's scope is too narrow.**
    - `resolve_column` is also called at `students.py:193`, which resolves the Grade column for the `student_rostering_grades` PII scope. It is referenced at `preflight.py:18` and at CLAUDE.md :176 ("Grade column via `resolve_column`"). None of these are listed.
    - D8's measurement covers bundled configs only. A bare string that is now honoured changes a USER-DIR config's scope with no gate (CLAUDE.md flags a v2.x user-dir `sd75myedbc`).
    - Fix:
      - add the files;
      - when a key's new resolution differs from the old default, log ONE WARNING per key in config spelling;
      - extend D8 to say so.
  - **13. S1 has a vacuous-grep AC.**
    - `convert.py:118-120` imports `RunErrorCategory` inside a parenthesised `from src.etl.pipeline import (…)`. A grep for the one-line form comes back empty while the import survives.
    - Fix: an AST pin that no `ImportFrom(module="src.etl.pipeline")` names `RunErrorCategory` or `ROSTER_ANCHOR_ENTITY` anywhere under src/, tests/ or scripts/.
  - **14. S1 states the current carrier convention backwards.**
    - "Existing carriers keep passing enum members, never `.value`" is backwards. Today the integrity carriers pass `.value` (`pipeline.py:456,467`), `OutputWriteError` stamps `.value` (`:401`), and `_INTEGRITY_FAULT_STATUSES` keys on `.value` (`convert_result.py:209-212`).
    - Fix: state the chosen direction (members everywhere; `.value` only at the one record normalisation point) and list these sites.
  - **15. S0: test and doc wiring.**
    - S0 says "Tests: none", but its AC updates `_EXPECTED_QUESTION_COUNTS` (`tests/test_output_contract_doc.py:132`), which needs verbatim text pins for Q5a–e. Add that file.
    - `output-contract.md:611` already says "🔴 Settle Q1b FIRST". "Q5 FIRST" re-ranks the owner's questions, so fold that into D2.
    - §5 routes `grades.py:393-397` to S10, but S1 now owns it.
    - `split_by_homeroom_grades`' KeyError (`grades.py:~329`) is missing from the catalogue.
  - **16. The "Architecture & holistic fit" section is missing.**
    - `docs/claugentic-PLAN_TEMPLATE.md:22-30` requires it for substantial work, with dimensions mapped to REAL modules. The content already exists, scattered across Approach and "Answers".
    - Add the section with this mapping:
      - `reliability-resilience`: the bulkhead.
      - `data-and-persistence`: the additive key, no DDL.
      - `security`: the PII-in-exception floor.
      - `observability-ops`: the grep anchor and the level trigger.
      - `product-ux`: the PARTIAL rung.
      - `testing`: parity, totality and AST pins.
      - `maintainability-structure`: the errors/outcomes/columns leaves.
      - `api-and-contracts`: output-contract 2.7.0 and the exit-code contract.
      - `docs-traceability`: failure-policy.md and the parity pins.
      - `performance-efficiency`: S13's drift-matrix CI cost.
  - **RECOMMENDED: should fix; not blocking.**
  - **17. S5 changes the Home verdict for a failed manual attempt (new owner decision D14).**
    - Its AC "[older success, newer failed manual attempt] → FAILED_ETL" means a test Convert of the wrong district after a good nightly turns Home to "Last sync failed".
    - There is precedent (`convert.py:332-338` records integrity refusals), but widening it to every exception is a product call. Proposed D14: record for Run History; the owner decides the effect on the Home verdict.
  - **18. S4 test naming and the fixture route.**
    - `tests/test_ui_flet_convert.py` does not exist. The convert/deliver tests live in `tests/test_ui_flet_convert_output.py`, `tests/test_anomaly_vanish_and_archive.py` and `tests/test_pipeline_run_store.py`.
    - The byte-identity AC ("Family removed from `enabled_entities`") needs a tmp user-dir overlay under `DISTRICTSYNC_DATA_DIR`, never a 21st config: `tests/test_contract.py:1313` pins `_DISTRICT_SETUP` to the bundled set. Say so.
  - **19. S4 partner-doc literal parity.** If troubleshooting.md or faq.md quote the PARTIAL headline or any `failure_copy` string, CLAUDE.md's literal-parity rule applies, and the existing closed-list sweeps won't catch it (`test_partner_doc_schedule_copy_parity.py:174-175`, `test_creator_doc_copy_parity.py:84-99`). Add a pin, or state "paraphrase only".
  - **20. S11 vs the S2 AST pin.**
    - `context.record_outcome_note(...)` is a method-call mutation that the "`context.<attr> =` only in CRITICAL modules" pin cannot see. Family is ISOLATABLE and will call it (`filter_to_active` `base.py:366-368`, `family.py`).
    - Fix: extend the pin to method-call mutations, or roll back a FAILED entity's notes beside `rollback_data_errors`. Word S4's INVARIANTS entry to match what is actually pinned.
  - **21. Release reachability for Unity (S4 Design 10 / D7).**
    - The nightly runs the exe path registered at schedule time (`windows.py:627` `Path(sys.executable)`).
    - A release helps Unity only if the new exe replaces the old one in place, or the Schedule is re-saved. Add that to D7's partner action and to the S4 measurement.
  - **22. Minor corrections.**
    - `test_pipeline_delivery_integrity.py:592-601` tests a demographic-only drop, not plan 0052's all-unroled exclusion (Problem ¶, D6).
    - S7 cites the privacy rule as `home_status.py:15-26`; it is `:22-26`.
    - `PipelineResult.entity_outcomes = ()` defaults a creator-activation gate input to "nothing failed". Prefer no default, or `None` ⇒ gate FAILED.
    - `students.py:282-286` (email `derived_dates`) is typed JOIN_KEY. Give a one-line rationale, or add a guard kind.
    - D11 should say plainly that a hand-dropped legacy user-dir config with any extra key on a field dict will stop its nightly.
    - CLAUDE.md's "`run_pipeline` returns `PipelineResult` (…)" bullet needs `entity_outcomes` (S2).
    - S3's test "no `{` in any string" must be scoped to `FAILED_CATEGORY_COPY`, because `outcome_sentence` interpolates the entity phrase.
  - **Owner decisions.**
    - D1–D13 are genuinely the owner's, and each recommendation is defensible. On D3, the plan should say explicitly that exit 0 hides a partial nightly from Task Scheduler's Last Run Result.
    - The plan itself decided two things that are not the plan's to decide: S4.4 (a delivery-shape change, see #1) and the S5 Home-verdict effect (#17). It should also carry the Q5-vs-Q1b priority (#15).
- **Sizing/completeness:**
  - **S0: OK (M)** once #2 and #15 are fixed. Keep it from becoming a treatise:
    - rows only for ENFORCED behaviour and for S1–S4 PLANNED;
    - no rows for the owner-gated S7/S8/S14/S15 until their gate passes, or the doc describes slices that may be declined;
    - cut §14, which duplicates "Answers";
    - fold §2 into §1.
    - What a future agent needs is the Naming table + §3 + §5 + §13.
    - INVARIANTS gets the S4.9 and S6 entries only, not one per P-rule. That would duplicate failure-policy.md, and INVARIANTS holds prose "must-hold" entries, not a policy index.
  - **S1: OK (M)** with #3, #13 and #14. Add a case-collision `ExtractionError` twin (`extractor.py:100`) beside the unparseable one; both land in INPUT_UNREADABLE.
  - **S2: OK (M)** with #6 and #7.
  - **S3: M→L, OK** with #8 and #9. Design-skill DoD applies.
  - **S4: L.** OK only once #1 (S4.4) is dropped. Otherwise split out an S4c for deliver-from-disk that owns `convert_output.py`.
  - **S5: OK (S)**, pending D14.
  - **S6: OK (M).**
  - **S7/S8: OK (S)**, both gated.
  - **S9: OK (M)** with #12.
  - **S10: OK (M).**
  - **S11: OK (M)** with #20.
  - **S12: OK (S).**
  - **S13: split.**
    - S13a covers fitness functions + BLE + the count constant. Include the Makefile `validate-config` lockstep CLAUDE.md requires; the ci.yml literal is at `:66`, not `:65`.
    - S13b is the drift matrix (20 configs × entities × guarded columns, each a pipeline run, on three OSes), with a stated CI time budget or marker.
  - **S14/S15: OK**, both gated.
  - **Conflict map: add these rows.**
    - `tests/conftest.py`: S2, S9.
    - `src/ui_flet/home_status.py`: S3, S8, S11.
    - `docs/partner/faq.md`: S0, S4, S14.
    - `src/ui_flet/convert_output.py`: S4, only if #1 is kept.
- **Harness impact:**
  - **ARCHITECTURE_TREE:** add lines for `src/etl/errors.py`, `src/etl/outcomes.py`, `src/etl/transformers/columns.py` and `src/ui_flet/failure_copy.py`. Update the `humanize.py` line if #8 moves the label maps there.
  - **CLAUDE.md**, slice by slice:
    - S0: +1 line.
    - S2: the `PipelineResult` bullet.
    - S4: the Exit-codes and row_filters sentences, in place.
    - S9: Configurable Columns and :176.
    - S12: the typo clause.
    - S15: the restructure (owner-gated).
  - **Config pins:** the 20-config pin, Makefile and `ci.yml:66` are untouched until S13, which must keep all three in lockstep. No slice changes `SUPPORTED_CONFIG_MINOR=13` or `TestDeclaredRangeVersusSupported`; S12 tightens the schema without adding a key. Confirmed.
  - **`scripts/ci_flet_pack_smoke.py`:** S4 touches it (#5).
  - **Contract doc:** `output-contract.md` goes 2.6.0 → 2.7.0 in S4 (2.6.0 verified current), and `_EXPECTED_QUESTION_COUNTS` moves in S0.
  - **Emails:** the only `check_no_emails` risk is doc text. Q5 and the troubleshooting edits must carry no address beyond the allow-listed support literal.
  - **Coverage and typing:** `failure_copy.py` is COUNTED for coverage, but mypy skips it (`src/ui_flet` is excluded). Acceptable; note it.
  - **ruff:** BLE is not selected today (`pyproject.toml:102-110`). There are 27 in-scope `except Exception`, 8 unannotated.
  - **Process:** the plan file is untracked (`?? .claude/plans/0053-…`). WORKFLOW's plan-file lifecycle says to commit it at draft; do so on a branch, and the owner merges.
  - **`Supersedes:` convention:** this is a harness-process change. Stage it in CANDIDATES rather than adopting it ad hoc in DECISIONS.
- **Re-review (plan-reviewer, 2026-09-23, same day, read-only, re-read in full):**
  - **Status per finding:**
    - **#1: PARTIALLY.** S4 Design 4 drops the filter and S4's AC keeps `deliver_job` byte-identical, which is right. But stale text still prescribes the dropped design:
      - Approach :64 still says "`deliver_job` honours the newest build's outcomes".
      - Risks :163 still says "S4 makes its manifest = newest record's BUILT set ∩ files present".
      - Decomposition :188 still lists "+ `deliver_job`".
      - Delete or rewrite all three.
    - **#2: ADDRESSED.** "Policies P1–P16" table at :74-93.
    - **#3: ADDRESSED.** S1's AC names `test_student_rostering_grades.py:163/:287` and their new assertions.
    - **#4: PARTIALLY.** `ConfigLoadError` exists (S1, and S5 (1)), but S1 also converts the PIPELINE config block (`pipeline.py:869-881`) from record + `sys.exit(1)` to `raise ConfigLoadError … from exc`.
      - That breaks `tests/test_pipeline_run_store.py:821-842`, which pins `SystemExit` code 1 for a config fault. Neither test is listed.
      - It would also double-record unless `_record_early_failure` is removed.
      - It regresses the creator gate: `gate_outcome_for` passes the pipeline error to `humanize_config_error` (`config_editor.py:1083`), which decides MISSING_BASE and UNREADABLE by `isinstance` (`:978-981`). A wrapper defeats both. `tests/test_ui_flet_config_editor.py:890` pins the `SystemExit` row.
      - Fix: leave the pipeline block exactly as today. It already stamps CONFIG at the sink, and no classifier sees that path. Wrap only Convert's `load_config` (S5 (1)); `error_card_copy` then classifies `ConfigLoadError` → CONFIG.
      - If the pipeline block must change anyway: list both tests, drop its `_record_early_failure`, and make `humanize_config_error` read `__cause__`.
    - **#5: ADDRESSED.** S4.5 uses the distinct `! not built:` line, adds the smoke to Files, tightens :897/:903, and adds a negative twin.
    - **#6: ADDRESSED.** S2 Files adds `conftest.py:760` and the three `test_pipeline_required_input.py` calls.
      - Nit: the conflict-map row calls `:760` a `build_run_record` call; it is a `run_transform` call.
    - **#7: PARTIALLY.**
      - The counts wording is fixed (:262).
      - The boundary rule contradicts itself:
        - :263 lists "no usable input" as ended-before-the-ledger (`None`), but S2 Files builds Convert's ledger BEFORE `load_data`. An `ExtractionError` (raised inside `load_data`, before the no-usable-input check) would then give `None` on the CLI but all-NOT_RUN on Convert, against S2's "Convert records carry the same outcomes the CLI would" and S5's `ExtractionError` parity.
        - :265 still says "`None` when the attempt ended before transform".
      - Fix: one construction point on BOTH entry points, immediately after `to_raw_dict()`. Every later raise → `finalize_aborted()`; `None` only for config-load and output-pre-flight exits.
    - **#8: PARTIALLY.** S3 Files moves the maps and the AC pins the cycle, but Design :283 still says `entity_phrase` "uses `home_status.ENTITY_LABELS`/`SIZE_NOUNS`". Change it to `humanize`.
    - **#9: ADDRESSED.** S3 Files, the walk-back AC twin, and S5 (0).
    - **#10: ADDRESSED.**
      - `DEPENDS_ON` now holds code dependencies only, without StudentCourses→CourseInfo, and S4 Design 1 has no withholding branch.
      - Nit: the stated reason ("falls back when history is empty") is not why. It is independent because `student_courses.py:112-114` reads the RAW `CourseInformation.txt`, not the CourseInfo output.
    - **#11: PARTIALLY.** The all-failed re-raise and its AC are in (S4 Design 1). Two stale lines contradict it:
      - S4 Design 3 :307: "Only-failed-entities → NO_OUTPUT".
      - S4 Risks :322: "category edge … NO_OUTPUT, accepted and documented".
      - Delete both.
    - **#12: PARTIALLY.** `students.py:193` and the local user-folder scan are in S9. Two things are still missing:
      - Deployed user-dir configs live on district servers, so no local scan can measure them. The runtime mitigation (ONE WARNING per key whose new resolution differs from the old default, config spelling) is missing.
      - S9 Docs names only "Configurable Columns"; CLAUDE.md :176 ("Grade column via `resolve_column`") also needs editing.
    - **#13: ADDRESSED.** AST sweep in S1's AC.
    - **#14: ADDRESSED.** S1 :240. Nit: :234 still reads "Existing carriers keep passing enum members". Change "keep" to "will".
    - **#15: ADDRESSED.** S0 Files and AC; Q5 after Q4 with a priority note; grades → S1; `split_by_homeroom_grades` row added. Nit: S0's "Tests: none" line now contradicts its own AC.
    - **#16: PARTIALLY.** The section exists and its codebase-fit content is good. The template's required "Quality dimensions to uphold — each mapped to its REAL `docs/claugentic-standards/` module" bullet is absent (`docs/claugentic-PLAN_TEMPLATE.md:27`), as are one-line product fit and future-proofing. Add the mapping listed in #16 above.
    - **Recommended #17 → D14: ADDRESSED.** D14 row; S5's AC defers to it.
    - **Recommended #21 → release note: ADDRESSED.** Execution scaffolding :175.
  - **New inconsistencies:**
    - (a) The Architecture section's direction `errors ← outcomes` means `outcomes` imports `errors`. So `reason_for` must live in `outcomes.py` (S1's own cycle clause), but the Naming table still homes it in `errors.py`. Decide in the table.
    - (b) The Status line claims "6 recommended — all applied", but #18 (S4 Tests :318 still cites the non-existent `test_ui_flet_convert.py`), #19 (troubleshooting literal parity), #20 (S11 `record_outcome_note` vs the S2 context-assignment AST pin) and #22 (:35 and D6 still attribute `:592-601` to plan 0052) are open. Say so, or apply them.
    - (c) `PipelineResult.entity_outcomes = ()` now contradicts the plan's own P8 ("a default only where it errs toward MORE warnings"): an empty default PASSES the creator gate.
  - **Verdict: CHANGES REQUIRED (narrow).**
    - No redesign is needed. What remains is text-level plus one substantive correction (#4's pipeline half) and one half-spec (#7).
    - None blocks Gate A: the owner can approve §3/§5 and D1–D4 now.
    - #4 and #7 must be fixed before S1/S2 start. The stale lines (#1, #8, #11, #14) and #12/#16 must be fixed before Stage 4 spec sign-off.
    - PASS once those edits land; no further review round needed beyond a diff check of those lines.
  - **Final check (plan-reviewer, 2026-09-23, read-only): CHANGES REQUIRED — four one-line edits, then PASS on a diff check alone.**
    - **Verified fixed:**
      - #4: S1 :242 leaves the pipeline config block untouched; the S1 AC is reworded.
      - #7: S2 :260 and :267 create the ledger after `to_raw_dict()` on both paths.
      - #1: spine :64 and Risks :167 now say `deliver_job` is unchanged.
      - #8: :287. #11: :311 and :326. #14: :238.
      - #12: S9's transitional WARNING.
      - #16: the dimension → module mapping, product fit and future-proofing.
      - `reason_for` now lives in `outcomes.py` (Naming table and S1 :245).
      - The creator gate passes only when every configured entity is BUILT or EMPTY.
      - #18 (:322), #19, #20 (S11 :410 + the S2 assignment-only pin), #22 (:35, D6).
    - **Remaining:**
      - (1) S5 :334 still says the pipeline block is one "which S1 already converted". That is now false; S1 leaves it untouched.
      - (2) Decomposition :192 still lists "+ `deliver_job`" under S4.
      - (3) S9 Docs calls CLAUDE.md :176 "the `record.get("final mark")` example line", but that example is at CLAUDE.md :236. :176 is the `student_rostering_grades` paragraph whose "Grade column via `resolve_column`" must change. Name both lines.
      - (4) Making `PipelineResult.entity_outcomes` and `ConvertResult.entity_outcomes` REQUIRED needs `field(kw_only=True)` on both, because every other field has a default and a non-default field after default fields is a `TypeError` at class definition. It also breaks unlisted constructors:
        - `PipelineResult(`: 20 sites in `test_app_config_creator.py`, `test_ui_flet_config_editor.py`, `test_ui_flet_creator_flow.py`, `test_ui_flet_routing.py` (plus the listed `test_pipeline_required_input.py`).
        - `ConvertResult(`: 26 test sites in `test_ui_flet_convert_result.py` ×22, `test_ui_flet_render_smoke.py` ×3, `test_ui_flet_humanization_sweep.py` ×1, plus 10 in `convert.py`.
        - Fix: add those files to S2 Files, or extend its "grep before starting" clause to both constructors.
    - **Scope:** none of the four affects Gate A or the design.

---

## Provenance

Research workflow `wf_e90e9a97-c7f` (2026-09-23; 112 agents; 5 Sonnet readers → 2 Opus auditors → 3 Opus designers → 2 Opus judges → 99 Opus refuters over 33 claims → 1 completeness critic). The two judges independently chose the risk-first design and grafted architecture-first's typed model + creator-gate fix and partner-safety-first's Q5/own-file/labels rules. Every one of the 33 synthesised claims drew at least one refutation (refuters defaulted to "refuted" when uncertain); the refutations that survived the orchestrator's judgement are folded into the slices above as explicit ACs (StrEnum persistence; instance-category not ClassVar; `compute_anomalies` unchanged to keep Convert's consent gate; `deliver_job` honours outcomes; no observed headers in exception/log text; S5's try-scope ends before SFTP; the `{value: ""}` remediation dropped; single-file-only labels; origin-keyed unknown keys; explicit `FLET_FREE_MODULES`; annotate-never-narrow BLE; count literal in ci.yml; the alumni-signal honesty note). Rejected refutations: "P16 is false from day one" (true of the claim as phrased; fixed by the explicit list) and "H2 reverses 0052" (fixed by the detected-fault precondition, not by dropping H2).
