# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GDE2Acsv is a Python ETL tool that converts MyEducation BC General Data Extracts (GDEs) into SpacesEDU Advanced CSV format. It processes GDE files (CSV or TXT, varies by district) and produces 5 output CSV files (Students, Staff, Family, Classes, Enrollments). Distributed as single-file executables via PyInstaller for non-technical school district users running on district servers with task schedulers.

## Commands

### Run (development)
```bash
python -m src.main --sis myedbc --input data/input --output data/output
```

CLI flags: `--dry-run` (preview without writing), `--diff` (compare against existing output), `--quality` (data quality report), `--sftp` (upload output CSVs via SFTP after run).

### Tests
```bash
python -m pytest tests/ -v                    # all tests
python -m pytest tests/ --cov=src --cov-report=term-missing --cov-fail-under=80  # with coverage
```

294 tests, 91% coverage. Coverage omits `src/utils/logger.py` and `src/ui/*` (configured in `pyproject.toml`). Benchmarks deselected by default (`-m 'not benchmark'` in addopts).

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
make validate-config  # validates all 5 district configs: myedbc, sd40, sd48, sd51, sd74
```

### Streamlit web UI
```bash
streamlit run src/ui/Home.py
```

### Build executables
```bash
make build-win     # Windows .exe (run on Windows)
```

Linux/macOS builds are produced by GitHub Actions on tag push. PyInstaller hidden imports: `pandas`, `yaml`, `logging.config`, `pydantic`, `pydantic_core`, `paramiko`, `keyring`.

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

### Loader (`src/etl/loader.py`)
Writes DataFrames to CSV (UTF-8 with BOM) with field ordering from YAML config. `save_all()` uses atomic transactional writes: stages to `.tmp_<timestamp>/`, commits all on success, rolls back on failure.

### Config (`src/config/`)
- `models.py` — Pydantic v2 models for YAML mapping validation. 8 field mapping types detected by `classify_field()`: direct mapping, transform, fixed value, academic year, append year, email format, name config, ID-role pair. EntityConfig also supports `headers` dict for headerless files.
- `loader.py` — YAML loading with `_base` inheritance (deep merge, cycle detection) and Pydantic validation. `load_config(sis_type)` returns a validated `MappingConfig`.

### Quality (`src/quality/report.py`)
`DataQualityReport` checks: missing/empty fields (>50% threshold), duplicates per entity-specific keys, orphaned enrollments (class or user not found), grade distribution anomalies.

### Web UI (`src/ui/`)
Multi-page Streamlit app. `Home.py` is the landing page with status dashboard. Pages:
- `pages/01_Setup_Wizard.py` — 5-step setup wizard (paths, district, schedule, SFTP, activate) with management dashboard for viewing/editing settings post-setup. Schedule and SFTP are optional. District names read from YAML `district_name` field.
- `pages/02_Convert.py` — Ad-hoc conversion with session_state persistence, quality report, missing file warnings. Uses `load_config()` with `_base` inheritance.
- `pages/03_Run_History.py` — Parses `__GDE2ACSV_RUN__` JSON log tags for tabular run history
- `pages/04_Mapping_Editor.py` — 7-step visual wizard for creating/editing district mapping configs without YAML. Uses `mapping_helpers.py` for column detection, override diff, YAML generation.
- `pages/05_Help.py` — Reads markdown from `docs/` directory (single source of truth shared with MkDocs site)

### Supporting modules
- `src/config/app_config.py` — Runtime config (`~/.gde2acsv/config.json`); SFTP non-sensitive settings. Unix file permissions (0o700/0o600).
- `src/sftp/uploader.py` — `SFTPUploader` with paramiko SSHClient + OS keyring. Zips all CSVs into `gde2acsv_YYYY-MM-DD.zip` before upload. Host restricted to `ALLOWED_SFTP_HOSTS` (3 SpacesEDU servers).
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
- Academic year dates (dict with `use_academic_year` key)
- ID year-appending (dict with `append_year_to_id` key)
- Email format templates (dict with `format` key, e.g., `{student number}@sd40.bc.ca`)
- Name config (dict with `primary teacher flag`, `teacher last name`, `course title`, `section letter`)
- ID-role pair (dict with `student_id_col` and `staff_id_col`)
- Headers for headerless files (dict with filename -> column name list)

5 district configs: `myedbc` (base), `sd40myedbc` (New Westminster — CSV files, headerless schedule), `sd48myedbc` (Sea to Sky), `sd51myedbc` (Boundary), `sd74myedbc` (Gold Trail). All use `_base: myedbc` inheritance.

## Key Data Flow

- **Students** — Filtered to active-only (enrollment status "Active"/"PreReg", or no withdrawal date). Withdrawal dates parsed in 4 formats.
- **Classes** — Join schedule + course info + staff info + optionally class info (for blended). Homeroom classes auto-generated for configured grades. Class names truncated to 100 chars.
- **Enrollments** — Homeroom + subject + blended teacher enrollments. Deduplicated on Class ID + User ID + Role. Invalid teacher IDs ("nan", blank) filtered out.
- **Anomaly detection** — Warns if any entity drops >20% vs previous run output
- **Structured logging** — `__GDE2ACSV_RUN__` JSON emitted after each run with timing, counts, SFTP status
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

## Testing Conventions

- Tests in `tests/` directory, one file per concern (not one-to-one with source files)
- Fixtures in `tests/conftest.py` for shared test data
- Tests use pandas DataFrames directly — no file I/O in unit tests
- Mock datetime for school year tests: patch `src.etl.transformers.base.datetime`
- Config tests validate against real YAML files and test Pydantic model behavior
- CI: ruff check + ruff format + mypy (non-UI) + bandit + pytest (80% coverage gate) + config validation (all 5 districts)
