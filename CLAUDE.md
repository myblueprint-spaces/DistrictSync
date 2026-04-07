# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GDE2Acsv is a Python ETL tool that converts MyEducation BC General Data Extracts (GDEs) into SpacesEDU Advanced CSV format. It processes 6 input `.txt` files and produces 5 output `.csv` files (Students, Staff, Family, Classes, Enrollments). Distributed as single-file executables via PyInstaller for non-technical school district users running on district servers with task schedulers.

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

### Lint
```bash
ruff check src/ tests/        # check
ruff check src/ tests/ --fix  # auto-fix
```

### Validate configs
```bash
make validate-config
```

### Streamlit web UI
```bash
streamlit run src/ui/app.py
```

### Build executables
```bash
make build-win     # Windows .exe (run on Windows)
make build-linux   # Linux binary via Docker
```

PyInstaller requires hidden imports: `pandas`, `yaml`, `logging.config`, `pydantic`, `pydantic_core`.

### Documentation
```bash
make docs        # build MkDocs site to site/
make docs-serve  # live preview at http://localhost:8000
```

## Architecture

Classic ETL pipeline orchestrated by `src/main.py`:

```
GDE .txt files  -->  Extractor  -->  Transformer  -->  Loader  -->  CSV files
```

### Extractor (`src/etl/extractor.py`)
Loads `.txt` files with multi-encoding fallback (UTF-8 -> Latin1 -> CP1252) and auto-delimiter detection (comma/tab). Normalizes column names (lowercase + strip) immediately after loading.

### Transformer (`src/etl/transformers/`)
Entity-specific transformers using Strategy Pattern with a registry:

- `base.py` — Abstract `BaseTransformer` with shared utilities (grade mapping, school year determination, academic date calculation)
- `context.py` — `TransformContext` dataclass for cross-entity shared state
- `registry.py` — Maps entity names ("Students", "Staff", etc.) to transformer classes
- `students.py` — Active student filtering, CEDS grade mapping, email generation
- `staff.py` — Staff records with role mapping
- `family.py` — Parent/guardian contact extraction
- `classes.py` — Homeroom generation + subject classes + blended class integration
- `enrollments.py` — Student + teacher enrollment rows from schedule data
- `blended.py` — Blended class detection service (same teacher/time/location with 2+ grade levels -> merged class)

`src/etl/transformer.py` is a backward-compatible facade that delegates to the modular transformers. Existing code calls `DataTransformer().transform(df, entity_cfg, entity_name, raw_data, global_config)`.

### Loader (`src/etl/loader.py`)
Writes DataFrames to CSV with field ordering from YAML config. `save_all()` uses atomic transactional writes: stages to `.tmp_<timestamp>/`, commits all on success, rolls back (deletes temp dir) on failure.

### Config (`src/config/`)
- `models.py` — Pydantic v2 models for YAML mapping validation. 8 field mapping types detected by `classify_field()`: direct mapping (string), transform, fixed value, academic year, append year, email format, name config, ID-role pair.
- `loader.py` — YAML loading with `_base` inheritance (deep merge) and Pydantic validation. `load_config(sis_type)` returns a validated `MappingConfig`.

### Quality (`src/quality/report.py`)
`DataQualityReport` checks: missing/empty fields, duplicates per entity-specific keys, orphaned enrollments (class or user not found), grade distribution anomalies. Used via `--quality` CLI flag and tested in `tests/test_quality_report.py`.

### Web UI (`src/ui/`)
Multi-page Streamlit app. `app.py` is the landing page. Pages:
- `pages/01_Setup_Wizard.py` — 5-step setup wizard (paths, district, schedule, SFTP, activate)
- `pages/02_Convert.py` — ad-hoc browser-based conversion (upload files, download CSVs)
- `pages/03_Run_History.py` — parses `__GDE2ACSV_RUN__` JSON log tags for tabular run history

### Supporting modules
- `src/config/app_config.py` — runtime config (`~/.gde2acsv/config.json`); SFTP non-sensitive settings
- `src/sftp/uploader.py` — `SFTPUploader` with paramiko + OS keyring credential storage
- `src/scheduler/windows.py` — `schtasks.exe` wrapper for Windows Task Scheduler
- `src/scheduler/linux.py` — crontab wrapper using sentinel comment `# GDE2Acsv managed entry`
- `src/etl/column_names.py` — column name constants (avoid magic strings across transformers)

## Configuration-Driven Design

All field mappings are in YAML files under `config/mappings/`. The `--sis` CLI argument selects which mapping file to load (e.g., `myedbc` -> `myedbc_mapping.yaml`). Mappings support:
- Direct column mappings (string value)
- Transform functions (dict with `transform` key, e.g., `grade_to_ceds`)
- Fixed values (dict with `value` key)
- Academic year dates (dict with `use_academic_year` key)
- ID year-appending (dict with `append_year_to_id` key)
- Email format templates (dict with `format` key)
- Name position extraction (dict with `name_position` key)

District configs (sd48, sd51, sd74) can use `_base: myedbc` inheritance to override only what differs.

## Key Data Flow

- **Students** — Filtered to active-only (via enrollment status field or absence of withdrawal date)
- **Classes** — Join StudentSchedule + CourseInformation + StaffInformation + optionally ClassInformationEnh (for blended classes). Homeroom classes auto-generated for elementary grades.
- **Enrollments** — Merge StudentSchedule with StudentDemographic to produce both student and teacher enrollment rows
- All entity transformations use pandas DataFrames with `.copy()` to avoid mutation side effects

## Key Patterns

- **Strategy Pattern** for transformers — each entity type has its own transformer class registered in `registry.py`
- **TransformContext** — shared state across transformer invocations within a single pipeline run
- **Backward-compatible facade** — `DataTransformer` in `transformer.py` delegates to new modular transformers
- **Config inheritance** — district configs inherit from base via `_base` key with recursive deep merge
- **Pydantic validation** — all YAML configs validated at startup before any ETL processing begins
- **`to_raw_dict()`** — `MappingConfig.to_raw_dict()` converts the validated config back to raw dicts for the transformer pipeline; no YAML re-read needed
- **Entity order gotcha** — `global_config.entity_order` defaults to `[]` (not None). Use `global_config.get("entity_order") or list(mappings.keys())` — NOT `.get("entity_order", fallback)` which won't trigger on empty list

## Logging

Configured via `config/logging.conf`. Debug-level logs go to `etl_tool.log` (append mode); console shows WARNING+. Key events logged: file loading, record counts, blended class detection, homeroom generation, active student filtering.

## Testing Conventions

- Tests in `tests/` directory, one file per concern (not one-to-one with source files)
- Fixtures in `tests/conftest.py` for shared test data
- Tests use pandas DataFrames directly — no file I/O in unit tests
- Mock datetime for school year tests: patch `src.etl.transformers.base.datetime`
- Config tests validate against real YAML files and test Pydantic model behavior
