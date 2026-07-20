# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DistrictSync is a Python ETL tool that converts MyEducation BC General Data Extracts (GDEs) into SpacesEDU / myBlueprint+ Advanced CSV format. It processes GDE files (CSV or TXT, varies by district) and produces up to 7 output CSVs: the 5 SpacesEDU rostering files (Students, Staff, Family, Classes, Enrollments) plus 2 optional myBlueprint+ files (CourseInfo, StudentCourses), selected per-config via `global_config.enabled_entities` (see **Output Targeting** below). Distributed as single-file executables via PyInstaller for non-technical school district users running on district servers with task schedulers.

## Commands

### Run (development)
```bash
python -m src.main --sis myedbc --input data/input --output data/output
```

CLI flags: `--dry-run` (preview without writing), `--diff` (compare against existing output), `--quality` (data quality report), `--sftp` (upload output CSVs via SFTP after run).

### SFTP credential setup (headless / Docker / no-browser)
```bash
python -m src.main --sftp-configure                                 # interactive prompt
python -m src.main --sftp-configure --sftp-host H --sftp-user U --sftp-remote R  # headless (password from DISTRICTSYNC_SFTP_PASSWORD env var, --sftp-password-stdin, or prompt)
python -m src.main --sftp-test                                      # verify stored credentials
python -m src.main --sftp-show                                      # print saved config (no password)
```
Handlers live in `src/main.py` (`_sftp_configure`, `_sftp_test`, `_sftp_show`, `_read_sftp_password`). Host is validated against `validators.ALLOWED_SFTP_HOSTS`. Password is stored in the OS keyring (`KEYRING_SERVICE = "DistrictSync_SFTP"`); non-sensitive settings are written to `config.json` in the per-OS app-data dir (`paths.user_data_dir()` via `platformdirs` — Windows `%LOCALAPPDATA%\DistrictSync`, macOS `~/Library/Application Support/DistrictSync`, Linux `~/.local/share/DistrictSync`; a legacy `~/.districtsync` is auto-migrated once at startup with a `MOVED.txt` breadcrumb).

### Tests
```bash
python -m pytest tests/ -v                    # all tests
python -m pytest tests/ --cov=src --cov-report=term-missing --cov-fail-under=80  # with coverage
```

CI coverage gate 80% (`--cov-fail-under=80`). Coverage omits `src/utils/logger.py` and the `src/ui_flet` view glue (`shell`/`nav_rail`/`launcher`/`components`/`picker_field` + `screens/*`; configured in `pyproject.toml`). Benchmarks deselected by default (`-m 'not benchmark'` in addopts).

### Lint + Format
```bash
ruff check src/ tests/           # lint check
ruff check src/ tests/ --fix     # auto-fix lint
ruff format src/ tests/          # format (CI enforces via --check)
ruff format --check src/ tests/  # verify formatting matches CI
```

Requires ruff>=0.15. CI runs both `ruff check` and `ruff format --check`.

### Type Check
```bash
mypy src/ --exclude 'src/ui_flet'
```

Enforced in CI (non-UI modules). Requires `types-paramiko` and `types-PyYAML` stubs (in requirements-dev.txt).

### Security Scan
```bash
bandit -r src/ -q -c pyproject.toml
```

The `-c pyproject.toml` flag is REQUIRED — it applies the `[tool.bandit]` skips (B404/B603/B607). The bare form false-fails with 4 pre-existing Low subprocess findings in `src/scheduler/`. CI uses the `-c` form.

### Validate configs
```bash
make validate-config  # validates all 11 configs: myedbc, sd40, sd48, sd51, sd54, sd60, sd74, mbp_all, mbp_core, mbponly, sd51attendance
```

### Desktop UI (Flet)
```bash
python -m src.main   # no arguments → opens the native Flet desktop UI
```

### Build executables
```bash
make build-win     # Windows .exe (run on Windows)
```

Linux/macOS builds are produced by GitHub Actions on tag push. PyInstaller hidden imports: `pandas`, `yaml`, `logging.config`, `pydantic`, `pydantic_core`, plus the platform-specific keyring backend (`keyring.backends.Windows` / `keyring.backends.macOS` / `keyring.backends.SecretService` + `keyring.backends.libsecret`). `paramiko` and `keyring` are top-level imports in `src/sftp/uploader.py` so PyInstaller picks them up from static analysis — only the dynamically-discovered keyring backends still need explicit hidden-imports.

### Documentation
Docs live in `docs/` (Markdown). There is no docs-site build — the MkDocs/GitHub-Pages site was removed; user-facing help is the SpacesEDU Help Centre, linked from the Flet Help surface.

## Architecture

Classic ETL pipeline orchestrated by `src/main.py`:

```
GDE files  -->  Extractor  -->  Transformer  -->  Loader  -->  CSV files
                                                    |
                                              Anomaly Detection
                                              Structured Logging
                                              SFTP Upload
```

### Extractor (`src/etl/extractor.py`)
Loads GDE files with multi-encoding fallback (UTF-8 -> Latin1 -> CP1252) and auto-delimiter detection (comma/tab). Supports headerless files via `file_headers` parameter (column names injected from YAML config). Normalizes column names (lowercase + strip) immediately after loading.

### Transformer (`src/etl/transformers/`)
Entity-specific transformers using Strategy Pattern with a registry:

- `base.py` — Abstract `BaseTransformer` with shared utilities (grade mapping, school year determination, academic date calculation, `assign_class_ids()` shared by Classes+Enrollments). Has `ALLOWED_TRANSFORMS` allowlist for security.
- `context.py` — `TransformContext` dataclass for cross-entity shared state
- `registry.py` — Maps entity names ("Students", "Staff", etc.) to transformer classes
- `students.py` — Active student filtering (enrollment status + withdrawal date with 4 date formats), CEDS grade mapping, email generation
- `staff.py` — Staff records with role mapping (Y=teacher, else=administrator)
- `family.py` — Parent/guardian contact extraction
- `classes.py` — Homeroom generation + subject classes + blended class integration
- `enrollments.py` — Student + teacher enrollment rows from schedule data; `.copy()` before mutations
- `blended.py` — Blended class detection (same teacher/time with 2+ grade levels -> merged class). Falls back to deduplicated schedule when ClassInfo lacks required columns.
- `course_info.py` — (myBlueprint+, opt-in) Course catalog from CourseInformation.txt; pattern-excludes K/early-grade/X/ATT codes; uses `apply_field_map` (config-driven).
- `student_courses.py` — (myBlueprint+, opt-in) Per-student transcript joining course history + selection + info; retake/in-progress/passed dedup; config-driven source columns since W4b1 (field_map + optional `source_columns:` block).

### Loader (`src/etl/loader.py`)
Writes DataFrames to CSV (UTF-8 with BOM) with field ordering from YAML config. `save_all()` uses atomic transactional writes: stages to `.tmp_<timestamp>/`, commits all on success, rolls back on failure.

`save_all` commit is **backup-and-restore atomic** (`_commit_staged`): each existing target is moved into `.bak_<ts>/` then the staged file promoted with `os.replace` (atomic same-fs overwrite, not `shutil.move` — fixes the Windows copy2+unlink within-file tear); any mid-commit failure rolls back (restore originals / remove new files) so the output dir is **never left torn**. Rollback runs inside the `except` and re-raises *before* the `finally` rmtrees `.bak_<ts>/` (restore-before-cleanup invariant).

### Config (`src/config/`)
- `models.py` — Pydantic v2 models for YAML mapping validation. 8 field mapping types detected by `classify_field()`: direct mapping, transform, fixed value, academic year, append year, email format, name config, ID-role pair. EntityConfig also supports `headers` dict for headerless files.
- `loader.py` — YAML loading with `_base` inheritance (deep merge, cycle detection) and Pydantic validation. `load_config(sis_type)` returns a validated `MappingConfig`.

### Quality (`src/quality/report.py`)
`DataQualityReport` checks: missing/empty fields (>50% threshold), duplicates per entity-specific keys, orphaned enrollments (class or user not found), grade distribution anomalies.

### Desktop UI (`src/ui_flet/`)
Native Flet 1.0 desktop app (no browser). `main.py`'s no-argv branch launches it via `src/ui_flet/launcher.py` (`boot_logging()` + one-time `migrate_legacy_data_dir()`) → `shell.py` (themed window + brand icon + left `NavigationRail` in ONE FIXED order — Home, Convert, Run History, Setup, Mapping, Help; D7 — with rail-follow on programmatic nav + a schedule-attention Setup badge). Six surfaces in `screens/`:
- `screens/home.py` — health dashboard: one plain-language verdict ("did the roster sync?") + metric tiles + off-thread schedule read-back (D4), or the first-run onboarding hero (single front door) when unconfigured.
- `screens/setup.py` — **first-run 5-step WIZARD** (District → Folders → Delivery → Schedule → honest adaptive finish; District leads per 2026-07-15 user decision, Delivery still precedes Schedule so `--sftp` is baked before registration); Schedule + Delivery are skippable. Run time uses a `ft.TimePicker` clock affordance (the TextField stays the value source of truth). Once completed, graduates to a flat **Settings** scroll (rail label stays "Setup") ordered folders/district → schedule → delivery + ONE reconciling Save (re-registers when any task-arg changed). Schedule → per-op UAC-elevated `register_task`; SFTP → side-effect-free test + keyring-on-Save-only. Neither password persisted to `AppConfig`. Only the wizard finish line flips `setup_completed`.
- `screens/convert.py` — run a conversion now on a worker thread (`job_runner.py`); explicit district (no `configs[0]` fallback, no input-dir fallback — fails loud on unset output); anomaly-ack write-gate; resolved-output caption + "Open folder"; optional SFTP delivery; writes a `source="manual"` run record.
- `screens/run_history.py` — read-only past runs from the SQLite run store (`src/history/store.py`), NOT log parsing; plain-language status (no raw error/path column); honest fresh-start empty state after this update.
- `screens/mapping.py` — review the active district config and switch to another pre-built one (NOT a YAML editor — the full editor is a ROADMAP item).
- `screens/help.py` — links out to the SpacesEDU Help Centre + support email.

Pure COUNTED (tested) logic: `tokens`/`theme`/`verdict`/`nav`/`humanize`/`home_status`/`schedule_status`/`schedule_probe`/`convert_result`/`convert_output`/`run_history`/`mapping_catalog`/`setup_errors`/`setup_gates`/`sftp_copy`/`setup_flow`/`job_runner` (the retired `run_log` parser is replaced by the `history.store` reader); view glue (`shell`/`nav_rail`/`components`/`picker_field`/`launcher` + `screens/*`) is coverage-omitted. Build all controls via `components.py` (see `docs/FLET_1.0_CONVENTIONS.md` — e.g. `ft.Dropdown` uses `on_select`, `ft.FilledButton` uses `content=`; the wrong forms raise `TypeError` on 0.85.3).

**Design system — `docs/DESIGN_SYSTEM.md` (authoritative) + the `districtsync-design` skill:** "Branded Professional" (Direction B). `tokens.py` = the ONLY hex/size source (`space_*`/`radius_*`/`type_*` scales + Direction B roles: navy rail, `color_content_wash`, toned status tints + deep on-tint text — all AA-gated in `UI_CONTRAST_PAIRS`); `components.py` = the ONLY control factories (`page_header`/`HealthVerdictBanner` verdict band/`metric_tile`/3-tier buttons [primary filled · secondary OUTLINED · text]/`card`/`district_chip`/`status_pill`). Rules: build via factories (never inline hex/size in screens), ONE filled primary per screen, verdict-first layout, toned bands not saturated fills. Skill triggers on any `src/ui_flet` change.

### Supporting modules
- `src/config/app_config.py` — Runtime config (`config.json` in `paths.user_data_dir()`); SFTP non-sensitive settings + `setup_completed`. `has_completed_setup()` (D4a) is the SINGLE durable finish-line (explicit flag OR `is_complete() and schedule_registered`, baked on load so no deployed install regresses into onboarding); `sis_type` defaults to `""` (D9 — no silent district). Unix file permissions (0o700/0o600).
- `src/history/store.py` — SQLite run store (`history.db` in `paths.user_data_dir()`; replaces the retired `run_log` log-parser). `write_run_record` = sole creator/migrator (schema + `user_version=1`, WAL/DELETE fallback, strictly non-fatal + post-commit, quarantine-recreate on corruption, never downgrades); `read_run_records` never creates the DB (missing/empty→`[]`, error→`None`, else newest-first flat dicts). Written by BOTH `run_pipeline` and `convert_job` (source ∈ manual/scheduled/cli/unknown); the `__DISTRICTSYNC_RUN__` log line stays as diagnostic parity (rich error detail there, bounded `error_category` only in the store). `store_meta().created_at` = fresh-start signal.
- `src/sftp/uploader.py` — `SFTPUploader` with paramiko SSHClient + OS keyring (both top-level imports). Zips all CSVs into `districtsync_YYYY-MM-DD.zip` before upload. Host restricted to `ALLOWED_SFTP_HOSTS` (3 SpacesEDU servers). Credential setup: the Flet Setup SFTP section (`src/ui_flet/screens/setup.py`) **and** headless CLI (`--sftp-configure` / `--sftp-test` / `--sftp-show` in `src/main.py`). Exposes `get_stored_password() -> str | None` (keyring read used by Setup to verify credentials are readable by the current account).
- `src/scheduler/windows.py` — `register_task` registers via PowerShell `Register-ScheduledTask` (a FIXED script referencing only `$env:DSYNC_*`, run via `-EncodedCommand` UTF-16LE-base64 — not stdin `-Command -`, which silently no-ops a multi-line try/catch on PS 5.1 — + a fresh-copy child env — `os.environ` never mutated); password supplied → explicit `-LogonType Password -RunLevel Highest` principal + `-User`/`-Password` (unattended), password only in child env `DSYNC_TASK_PW` — never on argv, never logged, and never injected by `register_task` into the returned message; no-password → `Interactive`/`Limited` (never S4U), `run_highest` ignored; fail-loud canonical msgs `"PowerShell not found"`/`"ScheduledTasks module not available"` else the PS error. **Errors are de-CLIXML'd** — the script's `catch` emits plain text via `[Console]::Error.WriteLine` (not `Write-Error`, which CLIXML-wraps a redirected stderr into a script-echoing blob), and `_clean_ps_stderr()` is a defensive Python fallback that extracts only the `<S S="Error">` message (never the script body / `DSYNC_TASK_PW` literal). `is_elevated()` (win32 `IsUserAnAdmin`, else False) lets the wizard's `_classify_schedule_error(msg, elevated)` distinguish un-elevated access-denied (→ run-as-admin) from elevated (→ batch-logon-right / wrong-password hint). the task action injects `--source scheduled` (env `DSYNC_SOURCE` fallback) so scheduled runs label correctly in the run store; a non-elevated `register_task` / `delete_task_elevated` self-elevates per-operation via `src/scheduler/elevation.py` (the app itself never runs elevated). `query_task` is RETIRED — schedule truth is `read_schedule() -> ScheduleReadback` (tri-state LIVE/MISSING/UNKNOWN via `Get-ScheduledTask` + `Get-ScheduledTaskInfo`, ~10s timeout), owned by the pure `ui_flet/schedule_status.py`; `delete_task` stays on `schtasks.exe`; inputs validated via `validators.py`; `current_run_as_user()` returns `DOMAIN\user`
- `src/scheduler/elevation.py` — Windows per-operation elevation IPC primitive (D5): `ShellExecuteExW("runas")` on the absolute System32 `powershell.exe` (bounded wait, never INFINITE), DPAPI CurrentUser inbound password blob (fail-closed on SID mismatch — never LocalMachine), plaintext atomic result (no secret), orphan-handshake sweep. Consumed by `windows.py`.
- `src/scheduler/linux.py` — crontab wrapper with `shlex.quote()` and sentinel comment
- `src/etl/column_names.py` — Column name constants (avoid magic strings across transformers)
- `src/utils/validators.py` — Centralized security: SIS type validation, task name validation, run time validation, SFTP host allowlist, shell quoting

## Configuration-Driven Design

All field mappings are in YAML files under `config/mappings/`. The `--sis` CLI argument selects which mapping file to load (e.g., `myedbc` -> `myedbc_mapping.yaml`). Mappings support:
- Direct column mappings (string value)
- Transform functions (dict with `transform` key, e.g., `grade_to_ceds`, `map_role`). Only `ALLOWED_TRANSFORMS` in `base.py` are permitted.
- Fixed values (dict with `value` key)
- Academic year dates (dict with `use_academic_year` key). Override with `use_academic_year: false` + `value: "YYYY-MM-DD"` for districts where auto-detection picks the wrong year (SD40, SD51, SD74 use this).
- ID year-appending (dict with `append_year_to_id` key)
- Email format templates (dict with `format` key, e.g., `{student number}@sd40.bc.ca`). Opt-in (default off → other districts byte-identical): `sanitize: true` reduces each substituted value to `[a-z0-9]`; `derived_dates: {pseudo: {column, date_format}}` injects a date part (e.g. `yy`) derived from a source date column into the template (reuses base date machinery; empty on blank/unparseable; fail-loud on a missing column). SD60 uses both to generate `{legal first}{legal surname}{admission yy}@learn60.ca`.
- Name config (dict with `primary teacher flag`, `teacher last name`, `course title`, `section letter`)
- ID-role pair (dict with `student_id_col` and `staff_id_col`)
- Headers for headerless files (dict with filename -> column name list)

`global_config.excluded_course_codes` (list[str]) filters schedule + class_info rows by Course Code (case-insensitive, trimmed) before class/enrollment/blended generation. SD40 uses `["ATT--AM", "ATT--PM"]` to drop MyEd BC's internal attendance-only sections. Applied in `base.filter_excluded_course_codes()` and called from `classes.py`, `enrollments.py`, and `blended.py` (the schedule-fallback path).

Base `myedbc` defines all 7 entity templates; configs select which to emit via `global_config.enabled_entities` (see **Output Targeting**). 7 SpacesEDU district configs — `myedbc` (base), `sd40myedbc` (New Westminster — CSV files, headerless schedule), `sd48myedbc` (Sea to Sky), `sd51myedbc` (Boundary), `sd54myedbc` (Bulkley Valley), `sd60myedbc` (Peace River North), `sd74myedbc` (Gold Trail) — each `_base: myedbc` and inherit the 5 rostering entities. 3 myBlueprint+ tier configs — `mbp_all` (all 7), `mbp_core` (Students + CourseInfo + StudentCourses), and `mbponly` (CourseInfo + StudentCourses only) — also `_base: myedbc`, overriding `enabled_entities`.

An entity may declare `row_filters` (list of `{column, include: [...]}`) to keep only matching rows before mapping — fails loudly if the column is missing (e.g. SD60's Family entity keeps only rows where `Parent Auth / Guardian = Y`, excluding non-guardian emergency contacts). `global_config.cross_enrollment.{collapse, home_school_column}` is an opt-in dedupe that collapses a student's duplicate cross-school rows to their home-school row while preserving enrollments/classes at every school the student attends (off unless a district config sets `collapse: true`).

## Key Data Flow

- **Students** — Filtered to active via the config-driven predicate in `BaseTransformer` (`is_active_mask`): status ∈ `active_values` (default `["Active", "PreReg"]`, the Advanced CSV spec's expected values; Inactive/etc. excluded, `EnrollStatus.active_values` overrides). Status wins when present; the withdraw date is only a fallback for rows with **no** status value (past/unparseable → Inactive, 4 formats). Publishes the active roster to `context.active_student_ids`.
- **Classes** — Join schedule + course info + staff info + optionally class info (for blended). Homeroom classes auto-generated for configured grades. Class names truncated to 100 chars.
- **Enrollments** — Homeroom + subject + blended teacher enrollments. Deduplicated on Class ID + User ID + Role. Invalid teacher IDs ("nan", blank) filtered out. **Zero-orphan invariant:** student rows (homeroom + subject) + homeroom-class creation are filtered to `context.active_student_ids` via `BaseTransformer.filter_to_active`, so no enrollment/class references a student absent from `Students.csv`; teacher rows are not filtered.
- **Anomaly detection** — Warns if any entity drops >20% vs previous run output
- **Structured logging + run store** — after each run the record is built ONCE → both the `__DISTRICTSYNC_RUN__` diagnostic log line (timing, counts, SFTP status, rich free-text error detail) AND the durable SQLite run store (`src/history/store.py`, bounded `error_category` only — privacy split). The store is the Run History source; the log stays ops diagnostics. Store writes are best-effort/non-fatal and never alter the exit-code contract
- **`run_pipeline` returns `PipelineResult`** (`entity_counts`, `sftp_attempted`, `sftp_ok`, `anomalies`); a requested SFTP upload that fails logs ERROR (`"SFTP upload FAILED — output files were NOT delivered to <host>"`) and `main` exits code **3** (ETL output still written, not rolled back)
- **Exit codes** — `0` success · `1` ETL/arg/validation error · `2` stdin empty or mutually-exclusive flags · `3` SFTP delivery failed (ETL output present). **No usable required input at all** (every required file missing/empty, checked right after `extractor.load_data`) → exit **1**; a *partial* run with some empty sources stays exit **0** by design (per-entity skip-on-empty is legitimate).
- **Fail-loud field transforms** — `apply_field_map` is **row-resilient**: a row whose transform raises blanks only **that cell** (valid rows keep their value), an unknown-transform / column-level error blanks **that column**; every failure is recorded to `context.data_errors` → surfaced as the run-log `data_errors` summary (`{total, by_field}`) + Run History "Completed with N data errors". Data errors are a **separate axis** — ETL `status` stays `success` and the run still delivers (never silently swallowed, never fails the run for one bad row).
- All entity transformations use pandas DataFrames with `.copy()` to avoid mutation side effects

## Security

- SFTP connections restricted to 3 known hosts via `validators.ALLOWED_SFTP_HOSTS`
- Scheduler inputs (sis_type, task_name, paths, run_time) validated before subprocess/crontab calls
- Transform dispatch uses the `ALLOWED_TRANSFORMS` allowlist (prevents arbitrary method invocation via YAML) — enforced FAIL-FAST at config load since W4b2 (single source: `src/config/models.py`; `base.py` keeps a defensive subclass-overridable runtime reference)
- Config file permissions set to 0o700/0o600 on Unix
- `bandit` security scan in CI

## Documentation

Docs live in `docs/` (Markdown) — partner + developer guides, read by the harness. The MkDocs/GitHub-Pages site was removed; the Flet Help surface links out to the SpacesEDU Help Centre rather than rendering bundled docs.

## Key Patterns

- **Strategy Pattern** for transformers — each entity type has its own transformer class registered in `registry.py`
- **TransformContext** — shared state across transformer invocations within a single pipeline run
- **Config inheritance** — district configs inherit from base via `_base` key with recursive deep merge and cycle detection
- **Pydantic validation** — all YAML configs validated at startup before any ETL processing begins
- **`to_raw_dict()`** — `MappingConfig.to_raw_dict()` converts validated config back to raw dicts for the transformer pipeline
- **Enabled-entities selection** routes through `MappingConfig.active_entities()` / `models.filter_enabled_entities` — never respell `enabled_entities or []`
- **Classes→Enrollments handoff** = the frozen `ClassArtifacts` bundle in `context.class_artifacts` (published once by Classes; Enrollments fails loud on absence)
- **Entity order gotcha** — `global_config.entity_order` defaults to `[]` (not None). Use `global_config.get("entity_order") or list(mappings.keys())`

## Engineering Principles (non-negotiable)

Priority order: **SOLID > DRY > KISS > YAGNI**. Keep layers isolated (UI / ETL-business / config-data).
- **Fail loudly.** Never swallow an exception to hide a config/column mismatch. The homeroom-enrollments bug (PR #12) was a caught `KeyError` that silently dropped rows — validate expected columns at transformer entry and raise/warn with an actionable message instead.
- **Validate at boundaries.** Pydantic validates configs at load; GDE inputs are untrusted — check for required columns rather than `KeyError`-ing mid-transform.
- **Single source of truth.** Never duplicate config, types, or constants across files.

The **full, reusable quality bar** — every dimension an implementation is held to (performance/caching, security/secrets, privacy/PII, resilience, concurrency, data integrity, observability, extensibility, i18n, …) — lives in **`docs/claugentic-ENGINEERING_STANDARDS.md`**, a *growing catch-all*. Per change, apply the **relevant** dimensions *fully* (never skip a relevant one; don't gold-plate irrelevant ones); you may **add** dimensions and may **justify a novel pattern** rather than be confined to known ones. Its **Current scope** section tracks which dimensions are live in DistrictSync *today* (a non-capping snapshot that grows with the stack).

## Configurable Columns (core rule)

GDE/source column names MUST come from the district `field_map` — never hardcoded in transformer code. Districts rename columns, so the mapping layer is the single source of truth.
- Map outputs via `BaseTransformer.apply_field_map(...)`. For direct column access, resolve the name from the entity's `field_map` with a sensible default — no inline literals like `record.get("final mark")`.
- The ONLY sanctioned hardcoded column names are the shared structural join keys in `src/etl/column_names.py` (`SCHOOL_NUMBER`, `MASTER_TIMETABLE_ID`, …). Add new shared keys there, not as scattered literals.
- `student_courses.py` is config-driven since 2026-07-20 (W4b1): output-keyed reads resolve through the field_map, auxiliary inputs through the optional per-entity `source_columns:` block, and `OUTPUT_COLUMNS` derives from the field_map keys.

## Output Targeting (`enabled_entities`)

`global_config.enabled_entities` decides which entities run → which CSVs are produced (empty/absent = all mappings, for back-compat). `entity_order` controls *ordering*; `enabled_entities` controls *inclusion*.
- All 7 entity definitions (5 SpacesEDU rostering + `CourseInfo` + `StudentCourses`) live in the base `myedbc_mapping.yaml`. Configs **select** via `enabled_entities`; they do **not** redefine entities.
- Tiers: `mbp_all` = all 7 (myBlueprint+), `mbp_core` = Students + the 2 course CSVs. SpacesEDU district configs (sd40/48/51/74) inherit the 5 rostering entities only.
- **Per-district myBlueprint+** = a thin config with `_base: <district>` + an `enabled_entities` that includes `CourseInfo`/`StudentCourses`. It inherits BOTH the district's column mappings AND the base entity definitions — which is *why* the entity defs live in the base.
- Adding a new output entity is multi-file — follow the checklist in `docs/developer/adding-transformer.md` (registry, base field_map+source_files, quality key_map, PyInstaller hidden-imports, enabled_entities, tests, ARCHITECTURE_TREE).
- **Stale entity CSVs (output-dir entity files not produced by the current run) are ARCHIVED into `archive_<ts>/`, NOT deleted** — `DataLoader.archive_stale_outputs` moves them aside via `os.replace` (non-destructive; excluded from SFTP's top-level `*.csv` glob, so they can't ship). Any future *delete*/prune must still derive from `enabled_entities`, never `mappings.keys()` — `_base` inheritance puts inherited-but-disabled entities (e.g. `CourseInfo`/`StudentCourses`) in `mappings.keys()`, so a `mappings.keys()`-keyed delete would erase a different config's legitimate CSV sharing the output dir (cross-config data loss). See `docs/claugentic-DECISIONS.md` (Plan 0008).

## Harness Discipline

- **Read `docs/claugentic-ARCHITECTURE_TREE.md` first** to locate files — don't explore the tree blindly. It's the single-source index (one line per source file).
- **Keep it current:** adding/moving/removing an indexed source file (`src/**/*.py`, `config/mappings/*.yaml`) requires updating `docs/claugentic-ARCHITECTURE_TREE.md` in the same change — with a one-line description. Enforced by `scripts/claugentic-check_architecture_tree.py`, wired as a **git `pre-commit` gate** (`.githooks/pre-commit` via `core.hooksPath=.githooks`, run `--staged`): a commit that adds/touches an in-scope file without a tree entry is **aborted** until the entry is added; **the agent that created the file authors the description** (a script can't write meaningful context). Runs once per `git commit` (no per-action overhead); requires `python`/`python3` on PATH.
- **Record non-trivial decisions** as dated one-liners in `docs/claugentic-DECISIONS.md`, and consult it before re-litigating a past choice.
- **Keep this file lean — it loads into every session.** Dense one-liners only; **index into the code and docs, don't duplicate them** — no pasted code, nothing an agent can read straight from the source, no restating `claugentic-WORKFLOW.md`/`claugentic-ENGINEERING_STANDARDS.md`. Add only commands, non-obvious gotchas, patterns, and project rules; point to the canonical doc rather than copy it.

## Development Workflow

Substantial work (new subsystem, cross-cutting refactor, shared-contract/pattern/standard change, security boundary, or ~8+ files) follows the staged pipeline in **`docs/claugentic-WORKFLOW.md`** — *triage → discuss → plan (`.claude/plans/`) → adversarial plan-review → spec → **user approval** → implement (isolated branch) → verify → land → retrospect* (see WORKFLOW.md for the gate detail, roles, and Definition of Done). Small/mechanical changes take the lightweight path (implement + verify). **Triage continuously:** the moment a conversation is shaping into substantial work, stop free-coding — ask questions, enter plan mode, then follow the pipeline.

Three rules are non-negotiable:
- **Slice small, land complete.** Every unit must be finishable by one specialist agent in a single ≤1M-context session and leave **no half-done state or new tech debt** — if it doesn't fit, decompose further.
- **Delegate liberally.** Use subagents freely and in parallel (no resource constraints) to preserve the orchestrator's context; the orchestrator picks whichever role(s) fit from the growing `.claude/agents/` library.
- **The harness is living.** Stage 9 feeds learnings back into STANDARDS/CLAUDE.md, the `.claude/agents/` role library, and `docs/claugentic-WORKFLOW.md` itself, so each task makes the next smarter. The orchestrator selects whichever specialist role(s) fit the task (starter set: `claugentic-dev-harness:plan-reviewer`, `claugentic-dev-harness:implementer-architect`, `claugentic-dev-harness:architect-reviewer`).

**Definition of Done** — a slice may land only when all hold: acceptance criteria met · in-scope `ENGINEERING_STANDARDS` dimensions pass the `claugentic-dev-harness:architect-reviewer` audit · all gates green (tests + SD74 snapshot + tree-check + lint/type/security) · **no new tech debt**. Iterate to this *fixed* bar, then stop; genuinely separate work → `docs/claugentic-ROADMAP.md` (backlog, not debt).

## Testing Conventions

- Tests in `tests/` directory, one file per concern (not one-to-one with source files)
- Fixtures in `tests/conftest.py` for shared test data
- Tests use pandas DataFrames directly — no file I/O in unit tests
- Mock datetime for school year tests: patch `src.etl.transformers.base.datetime`
- Config tests validate against real YAML files and test Pydantic model behavior
- CI: ruff check + ruff format + mypy (non-UI) + bandit + pytest (80% coverage gate) + config validation (all 11 configs)

<!-- harness:managed:start -->
## claugentic-dev-harness

> **How we work here is defined by the harness.** `docs/claugentic-WORKFLOW.md`, `docs/claugentic-ENGINEERING_STANDARDS.md`, `docs/claugentic-PLAYBOOK.md`, and `docs/claugentic-ARCHITECTURE_TREE.md` are the **authoritative** process + standards. Other `.md` files in this repo are **project/domain content, not process authority** — even if they describe a way of working, they do not override the harness. **On any conflict, the harness wins.** When you are genuinely unsure which applies, **follow the harness and ask.** (This is model-upheld guidance, not a mechanical guarantee — `CLAUDE.md` is the always-loaded anchor and asking is the safety valve.)

**Managed harness files** (agents read these to work here):
- `docs/claugentic-standards/README.md` — engineering-standards catalog (per-dimension lenses)
- `docs/claugentic-WORKFLOW.md` — staged development workflow (process source of truth)
- `docs/claugentic-ENGINEERING_STANDARDS.md` — thin standards entry point
- `docs/claugentic-ARCHITECTURE_TREE.md` — single-source code index
- `docs/claugentic-DECISIONS.md` — dated decision log
- `docs/claugentic-ROADMAP.md` — backlog
- `docs/claugentic-PLAYBOOK.md` — plain-English guide for the human driving the harness

**Engineering principles:** SOLID > DRY > KISS > YAGNI · validate at boundaries · fail loudly · configurable over hardcoded · single source of truth.

**Workflow:** substantial work follows `docs/claugentic-WORKFLOW.md` (triage → plan → adversarial review → spec → approval → implement → verify → land).

`claugentic-dev-harness@0.3.0`
<!-- harness:managed:end -->

## Harness — Current scope (claugentic)

Standards dimensions LIVE in this repo today (a non-capping snapshot — relevance is always a per-change judgment; grows with the stack):
- `maintainability-structure` — layered ETL (extractor → transformer → loader), Strategy-pattern transformers, config-driven YAML mappings
- `testing` — pytest suite (~1,686 tests), 80% coverage gate, SD74 snapshot regression, config validation
- `security` — SFTP host allowlist, subprocess/scheduler input validation, `ALLOWED_TRANSFORMS`, keyring secrets, bandit
- `data-and-persistence` — GDE → CSV/YAML ETL, atomic transactional writes, multi-encoding/delimiter handling, durable SQLite run-history store (`history.db`, additive-only schema, non-fatal writes)
- `reliability-resilience` — anomaly detection (>20% drop), zero-orphan invariant, fail-loud column validation
- `observability-ops` — structured `__DISTRICTSYNC_RUN__` JSON logging, documented exit-code contract
- `product-ux` — native Flet desktop UI (fixed nav: Home / Convert / Run History / Setup / Mapping / Help; first-run wizard graduating to Settings)

### DistrictSync scope tiers (harvested from the in-house standards doc, 2026-06-17)

**Key constraint:** DistrictSync is a **batch ETL tool — no database, no web API/server; SFTP egress only; PyInstaller exe distribution; handles student PII.** That shape decides which dimensions are LIVE vs deferred. These tiers are a **non-capping snapshot that grows as the stack grows** — promote a row when the stack changes (add a DB → Performance(DB) + Data-integrity go LIVE; add a web API → API design + authn/authz + tracing go LIVE; add threads/a queue → concurrency goes LIVE; move to metered cloud → Cost goes LIVE) and note it in `docs/claugentic-DECISIONS.md`. Never use a `NOT-YET` to skip a dimension genuinely relevant to a change.

- **LIVE (meet fully by default):**
  - **Privacy & data governance (student PII) — TOP PRIORITY:** no real data in repo, never logged, TLS via SFTP, FERPA-adjacent.
  - Correctness & resilience — encoding fallback, atomic writes + rollback, graceful skip; retries/backoff LIGHT (SFTP only).
  - Structure & design — Strategy/registry, `_base` inheritance, Pydantic.
  - DRY & reuse — `column_names.py`, shared `BaseTransformer`.
  - Security — keyring, host allowlist, `ALLOWED_TRANSFORMS`, scheduler-input validation, bandit.
  - Extensibility & maintainability — config-driven core (`enabled_entities`).
  - Observability & ops — `__DISTRICTSYNC_RUN__` records, anomaly detection; no PII in logs.
  - Data integrity — atomic writes, schema validation, orphaned-enrollment check, active-roster referential integrity (enrollments + homeroom classes filtered to `Students.csv`).
  - Testing — ~1,686 tests, SD74 snapshot regression, 80% gate.
  - Docs & traceability — architecture tree + decision log + `docs/` guides.
- **LIGHT (relevant but minimal today):**
  - Performance & efficiency — pandas memory (kill needless O(n²), vectorize, memoize lookups); DB/API tuning NOT-YET.
  - API & interface design — contracts = output-CSV schema + YAML config schema (version those); no HTTP API.
  - Internationalization — encoding fallback, date formats (DOB→ISO); timezones minimal.
  - Resources & concurrency — context managers, temp-dir cleanup; keep transformer singletons stateless.
  - Cost & resource use — district servers, not cloud-metered; watch memory on large GDEs.
- **NOT-YET (no current surface — don't gold-plate):**
  - DB / API performance tuning (no DB, no API).
  - User authn/authz (no server).
  - Multi-threaded concurrency (single-threaded batch run).
  - Cost (district servers, not metered cloud).

## Harness — Detected tooling (claugentic)

The project's own gates — the harness composes with these, it does not replace them:
- Lint/format: `ruff check src/ tests/` · `ruff format --check src/ tests/`
- Type-check: `mypy src/ --exclude 'src/ui_flet'`
- Tests: `python -m pytest tests/ -v` (80% coverage gate via `--cov-fail-under=80`)
- Security: `bandit -r src/ -q`
- Config validation: `make validate-config`
- CI: `.github/workflows/ci.yml`, `.github/workflows/release.yml`
- Run the app: `python -m src.main` (no args → native Flet desktop UI)
- Architecture tree: harness-skeleton (gate on)
- Harness mode: shared
- Competing way-of-work docs: reviewed (your init choice)
