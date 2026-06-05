# Roadmap

Backlog of substantial improvements. Each item runs through `docs/WORKFLOW.md` (triage → plan → review → spec → approve → implement → verify → land → retrospect) in **its own session**, sliced so each unit lands complete with no tech debt. Source: the full-codebase architect review (6-agent pass, 2026-06-04).

Status: `NEXT` (queued) · `LATER` · `PLAN <NNNN>` (plan drafted) · `DONE`.

---

## Tier 1 — Highest leverage (each unlocks several other fixes)

| # | Item | Principle | Leverage | Status |
|---|------|-----------|----------|--------|
| T1.1 | **Push the typed config through as a field-level Strategy.** Give each `Field*` model an `.apply(working, ctx) -> Series`; `apply_field_map` becomes a one-line dispatch; delete `to_raw_dict()`/`get_raw_field_map()`. | DIP/OCP/DRY | Kills the validate-then-discard round-trip + 4× duplicated dict-sniffing; new field type = one class; restores mypy past the boundary. | **PLAN 0001** |
| T1.2 | **Debloat `BaseTransformer` via composition.** Extract helpers (`grades`, `dates`, `course_codes`, `class_identity`, `ids`) into stateless modules; reduce base to the true contract; make `BlendedClassDetector` a plain service (not a `BaseTransformer`). | ISP/LSP/SRP | Dissolves the God-base, the `transform()`-raises LSP violation, and the `DataTransformer` facade. **Blocked by:** repointing legacy tests off the facade first (own slice). | NEXT |
| T1.3 | **Extract a shared pipeline core so the UI stops re-implementing it.** `transform_all(raw_data, config) -> dict[df]` + `load_from_bytes`; move `build_override_dict`/`_diff_dict` to `src/config/`; promote `ANOMALY_THRESHOLD`/`check_anomalies`/`compute_diff` to public pipeline API. | DRY/SoC | Removes the highest-risk drift (UI silently diverging from the pipeline). | NEXT |

## Tier 2 — Important, contained

| # | Item | Principle | Status |
|---|------|-----------|--------|
| T2.1 | **Make Classes→Enrollments handoff explicit.** Return a typed `ClassArtifacts` consumed by Enrollments instead of mutable `TransformContext` scratch; assert order / fail loud. Split context into immutable `RunConfig` + mutable cross-entity state. | Temporal coupling/SRP | LATER |
| T2.2 | **Make "add an entity" truly open/closed.** Data-drive `_emit_run_log` (iterate `outputs`); class-level `_DUPLICATE_KEYS` in quality report; single `config.active_entities()`; trust `--collect-submodules` over per-module hidden-imports. | OCP | NEXT |
| T2.3 | **Scheduler abstraction.** `Scheduler` Protocol + `get_scheduler()` factory in `scheduler/__init__.py`; collapse the 3 scattered `sys.platform` dispatch sites; share frozen-vs-source arg building. | OCP/DIP/DRY | NEXT |
| T2.4 | **Finish validate-at-boundary.** Normalize ID columns once at extraction (kills 22× `astype(str).str.strip()`); enforce `ALLOWED_TRANSFORMS` at config-load not mid-loop; fix/remove the dead `check_required_entities` validator; stop re-defending validated config. | Fail-loudly | NEXT |

## Tier 3 — Localized cleanups

| # | Item | Principle | Status |
|---|------|-----------|--------|
| T3.1 | `EnrollmentTransformer` → `EnrollmentSource` strategies (4 kinds, ~300 lines, mutable-list out-param). | SRP | LATER |
| T3.2 | Decompose `BlendedClassDetector.detect` (~95-line method); compute teacher lists with one groupby. | KISS | LATER |
| T3.3 | `student_courses.py`: derive `OUTPUT_COLUMNS` from `field_map.keys()` (single source). | DRY | NEXT |
| T3.4 | Hoist the grade→CEDS→homeroom split into one shared `BaseTransformer` method (Classes/Enrollments share it). | DRY | NEXT |
| T3.5 | `helpers.py` junk drawer: delete dead `validate_csv`/`validate_path`/`safe_float_conversion`; re-home `district_slug`/`build_zip_name`. | SRP/YAGNI | NEXT |
| T3.6 | Extractor: replace 9-way encoding×delimiter brute-force with a single sniff + validation (also removes silent wrong-delimiter risk). | KISS | LATER |
| T3.7 | `04_Mapping_Editor.py`: model wizard state as a `WizardState` dataclass (no `session_state` dict-drilling / attr-vs-dict mix); remove dead create/edit branch. | KISS | LATER |
| T3.8 | DRY idioms: `resolve_column()` helper (×12), shared `DATE_FORMATS` (×3), `step_labels()` in brand.py (×2), `footer()` (×4), `sys.path` bootstrap (×6). | DRY | LATER |
| T3.9 | Document `_deep_merge` list-replace semantics (contradicts "extend enabled_entities" prose). | KISS | NEXT |

## Process / hygiene (non-architecture)

| Item | Status |
|------|--------|
| `.gitattributes` (`* text=auto eol=lf`) + one normalization commit to end CRLF churn. | NEXT |
| Verify `PreRegSchoolCode` → "Next school code" against a real GDE header; add a test (else it silently blanks). | NEXT |
| Refine the 3 flagged `ARCHITECTURE_TREE.md` descriptions (`launcher.py`, `helpers.build_zip_name`, `tests/snapshots/config/`). | NEXT |
| **Unattended scheduled-run reliability** — task runs as setup user with stored password (`/RU /RP /RL HIGHEST`); SFTP failure exits 3; wizard verifies keyring readability. | **DONE** (plan 0002) |

## Scheduled-run reliability follow-ons (LATER)

| Item | Rationale |
|------|-----------|
| Service-account / machine-scope secret storage so SYSTEM or a gMSA can run the task (decouples the task from the interactive user's session entirely). | Non-goal for plan 0002; requires reworking `SFTPUploader` to support non-keyring secret sources. |
| Active alerting (email / Teams webhook) on SFTP failure instead of log-only — so admins are notified without checking Task Scheduler. | Log + exit-code 3 is sufficient for now; push-alerting is a separate integration concern. |
| Auto-run `--sftp-test` at the end of Setup Wizard to confirm delivery works before the first scheduled run. | Low-effort win deferred to keep Slice 3 scope tight; no blocking reason. |
| Wizard pre-validates the Windows password before finishing (call `schtasks /Query` or a lightweight auth check) to catch typos earlier. | Currently relies on `schtasks /Create` failing loudly; a pre-check would give a tighter feedback loop. |
