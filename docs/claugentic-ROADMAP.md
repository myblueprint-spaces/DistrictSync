# Roadmap (claugentic harness)

The prioritized backlog the claugentic harness works through. `/claugentic-dev-harness:audit` and `/claugentic-dev-harness:product` append their own fenced sections here; items in `## Later` are human-owned.

## Later

- **Config-driven columns tech debt** — `src/etl/transformers/student_courses.py` hardcodes ~10 source column names and bypasses its `field_map` for input (the field_map there only sets output column order). Migrate to fully config-driven source columns. _(Already tracked in the project's own `docs/DECISIONS.md`; surfaced here by the init harvest of `docs/WORKFLOW.md`.)_
- **Consider consolidating the two harnesses** — this repo now runs both its in-house harness and claugentic in parallel (two architecture trees, two tree-gate hooks). Decide whether to converge on one to reduce duplicate gates/maintenance. _(init observation, 2026-06-17.)_

- **Period-only attendance silently produces no output** (bug, found 2026-06-23 while planning 0006) — `run_transform`'s skip-on-empty-primary checks only the *positional first* source file, but `StudentAttendanceTransformer` resolves its daily/period bands *by role*. A period-only `sd51attendance` district (daily file empty/absent, period file present) gets the entity wrongly skipped -> no `StudentAttendance.csv`, run exits 0. Fix: make the skip-on-empty check role-aware (test all source files, not just `[0]`).

- **`append_year_to_id` (class-ID) field is not row-resilient** (follow-up to 0006, 2026-06-23) — in `apply_field_map` the `transform:` path now blanks only a failing row's cell, but the `append_year_to_id` branch (`base.py` ~737) uses `working.apply(..., axis=1)`; if `generate_class_id` raises on one row the whole column blanks (recorded loudly as a column-level data-error, not silent). Make it per-row resilient like the transform path for consistency.

- **Stale unmanaged output files** (found 2026-06-23 while planning 0007) — `DataLoader.save_all` only manages files it writes this run; a CSV left by a prior run whose entity was later dropped from `enabled_entities` persists in the output dir and could ship a stale entity. Pre-existing and orthogonal to the 0007 atomic-commit fix. Decide whether to prune-on-commit (remove managed-but-no-longer-emitted CSVs).

## Project-tracked backlog (harvested from the in-house harness, 2026-06-17)

Sourced from the full-codebase architect review (6-agent pass, 2026-06-04). Status markers: `NEXT` (queued) · `LATER` · `PLAN <NNNN>` (plan drafted). Each item runs through the workflow in its own session, sliced to land complete with no tech debt.

### Tier 1 — Highest leverage (each unlocks several other fixes)

- **T1.1 — Push the typed config through as a field-level Strategy.** Give each `Field*` model an `.apply(working, ctx) -> Series`; `apply_field_map` becomes a one-line dispatch; delete `to_raw_dict()`/`get_raw_field_map()`. _(DIP/OCP/DRY · kills the validate-then-discard round-trip + 4× duplicated dict-sniffing; new field type = one class; restores mypy past the boundary.)_ **Status: PLAN 0001**
- **T1.2 — Debloat `BaseTransformer` via composition.** Extract helpers (`grades`, `dates`, `course_codes`, `class_identity`, `ids`) into stateless modules; reduce base to the true contract; make `BlendedClassDetector` a plain service (not a `BaseTransformer`). _(ISP/LSP/SRP · dissolves the God-base, the `transform()`-raises LSP violation, and the `DataTransformer` facade. Blocked by: repointing legacy tests off the facade first — own slice.)_ **Status: NEXT**
- **T1.3 — Extract a shared pipeline core so the UI stops re-implementing it.** `transform_all(raw_data, config) -> dict[df]` + `load_from_bytes`; move `build_override_dict`/`_diff_dict` to `src/config/`; promote `ANOMALY_THRESHOLD`/`check_anomalies`/`compute_diff` to public pipeline API. _(DRY/SoC · removes the highest-risk drift — UI silently diverging from the pipeline.)_ **Status: NEXT**

### Tier 2 — Important, contained

- **T2.1 — Make Classes→Enrollments handoff explicit.** Return a typed `ClassArtifacts` consumed by Enrollments instead of mutable `TransformContext` scratch; assert order / fail loud. Split context into immutable `RunConfig` + mutable cross-entity state. _(Temporal coupling/SRP.)_ **Status: LATER**
- **T2.2 — Make "add an entity" truly open/closed.** Data-drive `_emit_run_log` (iterate `outputs`); class-level `_DUPLICATE_KEYS` in quality report; single `config.active_entities()`; trust `--collect-submodules` over per-module hidden-imports. _(OCP.)_ **Status: NEXT**
- **T2.3 — Scheduler abstraction.** `Scheduler` Protocol + `get_scheduler()` factory in `scheduler/__init__.py`; collapse the 3 scattered `sys.platform` dispatch sites; share frozen-vs-source arg building. _(OCP/DIP/DRY.)_ **Status: NEXT**
- **T2.4 — Finish validate-at-boundary.** Normalize ID columns once at extraction (kills 22× `astype(str).str.strip()`); enforce `ALLOWED_TRANSFORMS` at config-load not mid-loop; fix/remove the dead `check_required_entities` validator; stop re-defending validated config. _(Fail-loudly.)_ **Status: NEXT**

### Tier 3 — Localized cleanups

- **T3.1 — `EnrollmentTransformer` → `EnrollmentSource` strategies** (4 kinds, ~300 lines, mutable-list out-param). _(SRP.)_ **Status: LATER**
- **T3.2 — Decompose `BlendedClassDetector.detect`** (~95-line method); compute teacher lists with one groupby. _(KISS.)_ **Status: LATER**
- **T3.3 — `student_courses.py`: derive `OUTPUT_COLUMNS` from `field_map.keys()`** (single source). _(DRY.)_ **Status: NEXT**
- **T3.4 — Hoist the grade→CEDS→homeroom split into one shared `BaseTransformer` method** (Classes/Enrollments share it). _(DRY.)_ **Status: NEXT**
- **T3.5 — `helpers.py` junk drawer:** delete dead `validate_csv`/`validate_path`/`safe_float_conversion`; re-home `district_slug`/`build_zip_name`. _(SRP/YAGNI.)_ **Status: NEXT**
- **T3.6 — Extractor: replace 9-way encoding×delimiter brute-force with a single sniff + validation** (also removes silent wrong-delimiter risk). _(KISS.)_ **Status: LATER**
- **T3.7 — `04_Mapping_Editor.py`: model wizard state as a `WizardState` dataclass** (no `session_state` dict-drilling / attr-vs-dict mix); remove dead create/edit branch. _(KISS.)_ **Status: LATER**
- **T3.8 — DRY idioms:** `resolve_column()` helper (×12), shared `DATE_FORMATS` (×3), `step_labels()` in brand.py (×2), `footer()` (×4), `sys.path` bootstrap (×6). _(DRY.)_ **Status: LATER**
- **T3.9 — Document `_deep_merge` list-replace semantics** (contradicts "extend enabled_entities" prose). _(KISS.)_ **Status: NEXT**
- **T3.10 — `classify_field` is not idempotent** — re-validating an `EntityConfig` that already holds typed `Field*` values (e.g. a freshly-constructed `MappingConfig(mappings={"X": EntityConfig(...)})`) stringifies them (the `not isinstance(raw, dict)` → `str(raw)` branch). Harmless on the real `load_config` raw-dict path; surfaced building configs in code (plan 0003). Likely subsumed by T1.1 (which deletes `classify_field`); otherwise make it a no-op for already-typed values. _(Fail-loudly.)_ **Status: LATER**
- **T3.11 — `Family.csv` active filtering.** Family references `Student Number` from `EmergencyContactInformation` (not the demographic), so it can still emit contacts for withdrawn students — plan 0003's zero-orphan invariant covers Enrollments/Classes but not Family. Filter Family student refs to `context.active_student_ids` too. _(Data integrity.)_ **Status: LATER**
- **T3.12 — `StudentCourses` active filtering.** Transcripts are not filtered to the active roster (out of scope for plan 0003). Decide whether inactive students' course history should be emitted; if not, filter to `active_student_ids`. _(Data integrity.)_ **Status: LATER**

### Process / hygiene (non-architecture)

- **`.gitattributes` (`* text=auto eol=lf`) + one normalization commit** to end CRLF churn. **Status: NEXT**
- **Verify `PreRegSchoolCode` → "Next school code"** against a real GDE header; add a test (else it silently blanks). **Status: NEXT**
- **Refine the 3 flagged `ARCHITECTURE_TREE.md` descriptions** (`launcher.py`, `helpers.build_zip_name`, `tests/snapshots/config/`). **Status: NEXT**
- **Surface schedule students absent from the demographic** (e.g. SD48 `2644905`) as a data-quality warning — plan 0003's active filter silently drops their enrollment; a genuinely-active such student is a source gap worth flagging. **Status: NEXT**
- **Decide whether cross-school duplicate student rows in `Students.csv`** (same `User ID`, different `SchoolCode` — ~1,263 on SD48) are intended for the SpacesEDU import, or should be deduped/documented. Pre-existing; surfaced in plan 0003. **Status: NEXT**

### Scheduled-run reliability follow-ons (LATER)

- **Service-account / machine-scope secret storage** so SYSTEM or a gMSA can run the task (decouples the task from the interactive user's session entirely). _(Non-goal for plan 0002; requires reworking `SFTPUploader` to support non-keyring secret sources.)_
- **Active alerting (email / Teams webhook) on SFTP failure** instead of log-only — so admins are notified without checking Task Scheduler. _(Log + exit-code 3 is sufficient for now; push-alerting is a separate integration concern.)_
- **Auto-run `--sftp-test` at the end of Setup Wizard** to confirm delivery works before the first scheduled run. _(Low-effort win deferred to keep Slice 3 scope tight; no blocking reason.)_
- **Wizard pre-validates the Windows password before finishing** (call `schtasks /Query` or a lightweight auth check) to catch typos earlier. _(Currently relies on `schtasks /Create` failing loudly; a pre-check would give a tighter feedback loop.)_
