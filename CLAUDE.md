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
Handlers live in `src/main.py` (`_sftp_configure`, `_sftp_test`, `_sftp_show`, `_read_sftp_password`). Host is validated against `validators.ALLOWED_SFTP_HOSTS`. Password is stored in the OS keyring (`KEYRING_SERVICE = "DistrictSync_SFTP"`); settings are written to `~/.districtsync/config.json`.

### Tests
```bash
python -m pytest tests/ -v                    # all tests
python -m pytest tests/ --cov=src --cov-report=term-missing --cov-fail-under=80  # with coverage
```

640 tests; CI coverage gate 80% (`--cov-fail-under=80`). Coverage omits `src/utils/logger.py` and `src/ui/*` (configured in `pyproject.toml`). Benchmarks deselected by default (`-m 'not benchmark'` in addopts).

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
mypy src/ --exclude 'src/ui'
```

Enforced in CI (non-UI modules). Requires `types-paramiko` and `types-PyYAML` stubs (in requirements-dev.txt).

### Security Scan
```bash
bandit -r src/ -q
```

### Validate configs
```bash
make validate-config  # validates all 7 configs: myedbc, sd40, sd48, sd51, sd74, mbp_all, mbp_core
```

### Streamlit web UI
```bash
streamlit run src/ui/Home.py
```

### Build executables
```bash
make build-win     # Windows .exe (run on Windows)
```

Linux/macOS builds are produced by GitHub Actions on tag push. PyInstaller hidden imports: `pandas`, `yaml`, `logging.config`, `pydantic`, `pydantic_core`, plus the platform-specific keyring backend (`keyring.backends.Windows` / `keyring.backends.macOS` / `keyring.backends.SecretService` + `keyring.backends.libsecret`). `paramiko` and `keyring` are top-level imports in `src/sftp/uploader.py` so PyInstaller picks them up from static analysis — only the dynamically-discovered keyring backends still need explicit hidden-imports.

### Documentation
```bash
mkdocs serve       # live preview at http://localhost:8000
mkdocs build       # build static site to site/
mkdocs gh-deploy   # deploy to GitHub Pages
```

MkDocs auto-deploys to GitHub Pages on release (via release.yml).

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
- `student_courses.py` — (myBlueprint+, opt-in) Per-student transcript joining course history + selection + info; retake/in-progress/passed dedup. NOTE: currently hardcodes source columns and bypasses its `field_map` for input — see **Configurable Columns** (tech debt).

### Loader (`src/etl/loader.py`)
Writes DataFrames to CSV (UTF-8 with BOM) with field ordering from YAML config. `save_all()` uses atomic transactional writes: stages to `.tmp_<timestamp>/`, commits all on success, rolls back on failure.

### Config (`src/config/`)
- `models.py` — Pydantic v2 models for YAML mapping validation. 8 field mapping types detected by `classify_field()`: direct mapping, transform, fixed value, academic year, append year, email format, name config, ID-role pair. EntityConfig also supports `headers` dict for headerless files.
- `loader.py` — YAML loading with `_base` inheritance (deep merge, cycle detection) and Pydantic validation. `load_config(sis_type)` returns a validated `MappingConfig`.

### Quality (`src/quality/report.py`)
`DataQualityReport` checks: missing/empty fields (>50% threshold), duplicates per entity-specific keys, orphaned enrollments (class or user not found), grade distribution anomalies.

### Web UI (`src/ui/`)
Multi-page Streamlit app. `Home.py` is the landing page with status dashboard. Pages:
- `pages/01_Setup_Wizard.py` — 5-step wizard (schedule + SFTP optional). Shows management dashboard post-setup. District names from YAML `district_name` field.
- `pages/02_Convert.py` — Ad-hoc conversion with session_state persistence, quality report, missing file warnings. Uses `load_config()` with `_base` inheritance.
- `pages/03_Run_History.py` — Parses `__DISTRICTSYNC_RUN__` JSON log tags for tabular run history
- `pages/04_Mapping_Editor.py` — 7-step visual wizard for creating/editing district mapping configs without YAML. Uses `mapping_helpers.py` for column detection, override diff, YAML generation.
- `pages/05_Help.py` — Reads markdown from `docs/` directory (single source of truth shared with MkDocs site)

### Supporting modules
- `src/config/app_config.py` — Runtime config (`~/.districtsync/config.json`); SFTP non-sensitive settings. Unix file permissions (0o700/0o600).
- `src/sftp/uploader.py` — `SFTPUploader` with paramiko SSHClient + OS keyring (both top-level imports). Zips all CSVs into `districtsync_YYYY-MM-DD.zip` before upload. Host restricted to `ALLOWED_SFTP_HOSTS` (3 SpacesEDU servers). Credential setup: wizard Step 4 (`src/ui/pages/01_Setup_Wizard.py`) **and** headless CLI (`--sftp-configure` / `--sftp-test` / `--sftp-show` in `src/main.py`).
- `src/scheduler/windows.py` — `schtasks.exe` wrapper with input validation via `validators.py`
- `src/scheduler/linux.py` — crontab wrapper with `shlex.quote()` and sentinel comment
- `src/etl/column_names.py` — Column name constants (avoid magic strings across transformers)
- `src/utils/validators.py` — Centralized security: SIS type validation, task name validation, run time validation, SFTP host allowlist, shell quoting
- `src/ui/mapping_helpers.py` — Column detection from uploaded files, field metadata registry, override diff for `_base` inheritance, YAML generation

## Configuration-Driven Design

All field mappings are in YAML files under `config/mappings/`. The `--sis` CLI argument selects which mapping file to load (e.g., `myedbc` -> `myedbc_mapping.yaml`). Mappings support:
- Direct column mappings (string value)
- Transform functions (dict with `transform` key, e.g., `grade_to_ceds`, `map_role`). Only `ALLOWED_TRANSFORMS` in `base.py` are permitted.
- Fixed values (dict with `value` key)
- Academic year dates (dict with `use_academic_year` key). Override with `use_academic_year: false` + `value: "YYYY-MM-DD"` for districts where auto-detection picks the wrong year (SD40, SD51, SD74 use this).
- ID year-appending (dict with `append_year_to_id` key)
- Email format templates (dict with `format` key, e.g., `{student number}@sd40.bc.ca`)
- Name config (dict with `primary teacher flag`, `teacher last name`, `course title`, `section letter`)
- ID-role pair (dict with `student_id_col` and `staff_id_col`)
- Headers for headerless files (dict with filename -> column name list)

`global_config.excluded_course_codes` (list[str]) filters schedule + class_info rows by Course Code (case-insensitive, trimmed) before class/enrollment/blended generation. SD40 uses `["ATT--AM", "ATT--PM"]` to drop MyEd BC's internal attendance-only sections. Applied in `base.filter_excluded_course_codes()` and called from `classes.py`, `enrollments.py`, and `blended.py` (the schedule-fallback path).

Base `myedbc` defines all 7 entity templates; configs select which to emit via `global_config.enabled_entities` (see **Output Targeting**). 5 SpacesEDU district configs — `myedbc` (base), `sd40myedbc` (New Westminster — CSV files, headerless schedule), `sd48myedbc` (Sea to Sky), `sd51myedbc` (Boundary), `sd74myedbc` (Gold Trail) — each `_base: myedbc` and inherit the 5 rostering entities. 2 myBlueprint+ tier configs — `mbp_all` (all 7) and `mbp_core` (Students + CourseInfo + StudentCourses) — also `_base: myedbc`, overriding `enabled_entities`.

## Key Data Flow

- **Students** — Filtered to active-only (enrollment status "Active"/"PreReg", or no withdrawal date). Withdrawal dates parsed in 4 formats.
- **Classes** — Join schedule + course info + staff info + optionally class info (for blended). Homeroom classes auto-generated for configured grades. Class names truncated to 100 chars.
- **Enrollments** — Homeroom + subject + blended teacher enrollments. Deduplicated on Class ID + User ID + Role. Invalid teacher IDs ("nan", blank) filtered out.
- **Anomaly detection** — Warns if any entity drops >20% vs previous run output
- **Structured logging** — `__DISTRICTSYNC_RUN__` JSON emitted after each run with timing, counts, SFTP status
- All entity transformations use pandas DataFrames with `.copy()` to avoid mutation side effects

## Security

- SFTP connections restricted to 3 known hosts via `validators.ALLOWED_SFTP_HOSTS`
- Scheduler inputs (sis_type, task_name, paths, run_time) validated before subprocess/crontab calls
- Transform dispatch uses `ALLOWED_TRANSFORMS` allowlist (prevents arbitrary method invocation via YAML)
- Config file permissions set to 0o700/0o600 on Unix
- `bandit` security scan in CI

## Documentation

Single source of truth: `docs/` directory is read by both MkDocs (static site / GitHub Pages) and the Streamlit Help page (`05_Help.py`). Update docs in `docs/` — both renderers pick up the changes.

MkDocs deploys to GitHub Pages automatically on release tag push.

## Key Patterns

- **Strategy Pattern** for transformers — each entity type has its own transformer class registered in `registry.py`
- **TransformContext** — shared state across transformer invocations within a single pipeline run
- **Config inheritance** — district configs inherit from base via `_base` key with recursive deep merge and cycle detection
- **Pydantic validation** — all YAML configs validated at startup before any ETL processing begins
- **`to_raw_dict()`** — `MappingConfig.to_raw_dict()` converts validated config back to raw dicts for the transformer pipeline
- **Entity order gotcha** — `global_config.entity_order` defaults to `[]` (not None). Use `global_config.get("entity_order") or list(mappings.keys())`

## Engineering Principles (non-negotiable)

Priority order: **SOLID > DRY > KISS > YAGNI**. Keep layers isolated (UI / ETL-business / config-data).
- **Fail loudly.** Never swallow an exception to hide a config/column mismatch. The homeroom-enrollments bug (PR #12) was a caught `KeyError` that silently dropped rows — validate expected columns at transformer entry and raise/warn with an actionable message instead.
- **Validate at boundaries.** Pydantic validates configs at load; GDE inputs are untrusted — check for required columns rather than `KeyError`-ing mid-transform.
- **Single source of truth.** Never duplicate config, types, or constants across files.

The **full, reusable quality bar** — every dimension an implementation is held to (performance/caching, security/secrets, privacy/PII, resilience, concurrency, data integrity, observability, extensibility, i18n, …) — lives in **`docs/ENGINEERING_STANDARDS.md`**, a *growing catch-all*. Per change, apply the **relevant** dimensions *fully* (never skip a relevant one; don't gold-plate irrelevant ones); you may **add** dimensions and may **justify a novel pattern** rather than be confined to known ones.

## Configurable Columns (core rule)

GDE/source column names MUST come from the district `field_map` — never hardcoded in transformer code. Districts rename columns, so the mapping layer is the single source of truth.
- Map outputs via `BaseTransformer.apply_field_map(...)`. For direct column access, resolve the name from the entity's `field_map` with a sensible default — no inline literals like `record.get("final mark")`.
- The ONLY sanctioned hardcoded column names are the shared structural join keys in `src/etl/column_names.py` (`SCHOOL_NUMBER`, `MASTER_TIMETABLE_ID`, …). Add new shared keys there, not as scattered literals.
- Known debt: `student_courses.py` bypasses this (hardcodes ~10 source columns and ignores its `field_map` for input — the field_map there only sets output column order). Migrate to config-driven columns; see `docs/DECISIONS.md`.

## Output Targeting (`enabled_entities`)

`global_config.enabled_entities` decides which entities run → which CSVs are produced (empty/absent = all mappings, for back-compat). `entity_order` controls *ordering*; `enabled_entities` controls *inclusion*.
- All 7 entity definitions (5 SpacesEDU rostering + `CourseInfo` + `StudentCourses`) live in the base `myedbc_mapping.yaml`. Configs **select** via `enabled_entities`; they do **not** redefine entities.
- Tiers: `mbp_all` = all 7 (myBlueprint+), `mbp_core` = Students + the 2 course CSVs. SpacesEDU district configs (sd40/48/51/74) inherit the 5 rostering entities only.
- **Per-district myBlueprint+** = a thin config with `_base: <district>` + an `enabled_entities` that includes `CourseInfo`/`StudentCourses`. It inherits BOTH the district's column mappings AND the base entity definitions — which is *why* the entity defs live in the base.
- Adding a new output entity is multi-file — follow the checklist in `docs/developer/adding-transformer.md` (registry, base field_map+source_files, quality key_map, PyInstaller hidden-imports, enabled_entities, tests, ARCHITECTURE_TREE).

## Harness Discipline

- **Read `docs/ARCHITECTURE_TREE.md` first** to locate files — don't explore the tree blindly. It's the single-source index (one line per source file).
- **Keep it current:** adding/moving/removing an indexed source file (`src/**/*.py`, `config/mappings/*.yaml`) requires updating `docs/ARCHITECTURE_TREE.md` in the same change — with a one-line description. Enforced by `scripts/check_architecture_tree.py`, wired as a **`PostToolUse(Write)` nudge + `Stop` backstop** in `.claude/settings.json` (also `make check-tree`/CI): the script checks presence/staleness and prompts on a new/undocumented file; **the agent that created the file authors the description** (a script can't write meaningful context). Requires `python` on PATH; Claude Code will ask each dev to approve the project hooks on first use.
- **Record non-trivial decisions** as dated one-liners in `docs/DECISIONS.md`, and consult it before re-litigating a past choice.

## Development Workflow

Substantial work (new subsystem, cross-cutting refactor, shared-contract/pattern/standard change, security boundary, or ~8+ files) follows the staged pipeline in **`docs/WORKFLOW.md`**: triage → discuss/brainstorm → plan (`.claude/plans/`) → adversarial plan-review → spec → **user approval** → implement (isolated branch) → verify (tests + SD74 snapshot + `check-tree` + lint/type/security + `/simplify` + **architect-review vs `ENGINEERING_STANDARDS`**) → land & archive → **retrospect**. Small/mechanical changes take the lightweight path (implement + verify), still updating ARCHITECTURE_TREE/DECISIONS. **Triage continuously:** the moment a conversation is shaping into substantial work, stop free-coding — ask clarifying questions, enter plan mode, then follow the pipeline.

Three rules are non-negotiable:
- **Slice small, land complete.** Every unit must be finishable by one specialist agent in a single ≤1M-context session and leave **no half-done state or new tech debt** — if it doesn't fit, decompose further.
- **Delegate liberally.** Use subagents freely and in parallel (no resource constraints) to preserve the orchestrator's context; the orchestrator picks whichever role(s) fit from the growing `.claude/agents/` library.
- **The harness is living.** Stage 9 feeds learnings back into STANDARDS/CLAUDE.md, the `.claude/agents/` role library, and `docs/WORKFLOW.md` itself, so each task makes the next smarter. The orchestrator selects whichever specialist role(s) fit the task (starter set: `plan-reviewer`, `implementer-architect`, `architect-reviewer`).

**Definition of Done** — a slice may land only when all hold: acceptance criteria met · in-scope `ENGINEERING_STANDARDS` dimensions pass the `architect-reviewer` audit · all gates green (tests + SD74 snapshot + `check-tree` + lint/type/security) · **no new tech debt**. Iterate to this *fixed* bar, then stop; genuinely separate work → `ROADMAP.md` (backlog, not debt).

## Testing Conventions

- Tests in `tests/` directory, one file per concern (not one-to-one with source files)
- Fixtures in `tests/conftest.py` for shared test data
- Tests use pandas DataFrames directly — no file I/O in unit tests
- Mock datetime for school year tests: patch `src.etl.transformers.base.datetime`
- Config tests validate against real YAML files and test Pydantic model behavior
- CI: ruff check + ruff format + mypy (non-UI) + bandit + pytest (80% coverage gate) + config validation (all 7 configs)
