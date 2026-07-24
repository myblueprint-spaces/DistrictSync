# Architecture

DistrictSync is a classic ETL pipeline: **Extract → Transform → Load**. All entity-specific logic lives in pluggable transformer classes; configuration drives field mappings without code changes.

---

## Pipeline overview

```
GDE files
     │
     ▼
┌─────────────┐
│  DataExtractor │  src/etl/extractor.py
│  load_data()   │  multi-encoding, auto-delimiter, column normalize
└──────┬──────┘
       │ raw_data: dict[filename → DataFrame]
       ▼
┌─────────────────┐
│  DataTransformer │  src/etl/transformer.py  (facade)
│  transform()     │  delegates to entity transformer via registry
└──────┬──────────┘
       │ outputs: dict[entity_name → DataFrame]
       ▼
┌────────────┐
│  DataLoader │  src/etl/loader.py
│  save_all() │  atomic transactional write to output dir
└────────────┘
       │
       ▼
CSV files (5–7 depending on tier — see Output tiers below)
```

`src/main.py` orchestrates the three stages. It also handles CLI flags (`--dry-run`, `--diff`, `--quality`, `--sftp`) and calls `_sftp_upload()` after a successful write. After each run, anomaly detection checks whether any entity's record count dropped more than 20% compared to the previous run. Each run writes a machine-readable `__DISTRICTSYNC_RUN__` JSON log tag consumed by the Run History UI page.

### Output tiers

The base `myedbc_mapping.yaml` defines **all 7 entity templates** but a `global_config.enabled_entities` list controls which ones actually run. Three preset tiers ship in `config/mappings/`:

| Config | Enabled entities |
|---|---|
| `myedbc` (and inheriting district configs) | Students, Staff, Family, Classes, Enrollments |
| `mbp_all` | All 7 (above + CourseInfo + StudentCourses) — full myBlueprint+ tier |
| `mbp_core` | Students, CourseInfo, StudentCourses — minimal myBlueprint+ tier |

When `enabled_entities` is empty/missing, every mapping in the config is enabled (backward compat). The pipeline filters the entity loop by this list immediately after computing `entity_order`.

---

## Extractor

**File:** `src/etl/extractor.py`

- Loads each GDE file from the input directory.
- Tries UTF-8, Latin-1, CP1252 in sequence (MyEdBC files vary by district).
- Auto-detects comma vs tab delimiter using `csv.Sniffer`.
- Normalises column names immediately: lowercase + strip. All downstream code assumes lower-case column names.
- Supports headerless source files: if a source file has no header row, column names can be injected via the `file_headers` parameter in the district config.

---

## Transformer

### Strategy Pattern

Each entity type has its own transformer class that implements `BaseTransformer.transform()`. The registry maps entity names to instances:

```
TRANSFORMER_REGISTRY = {
    "Students":       StudentTransformer(),
    "Staff":          StaffTransformer(),
    "Family":         FamilyTransformer(),
    "Classes":        ClassTransformer(),
    "Enrollments":    EnrollmentTransformer(),
    "CourseInfo":     CourseInfoTransformer(),       # myBlueprint+ tier
    "StudentCourses": StudentCoursesTransformer(),   # myBlueprint+ tier
}
```

Entities not in the registry use `DefaultTransformer`, which applies the YAML field map generically without custom logic.

**File:** `src/etl/transformers/registry.py`

### BaseTransformer

Abstract base class (`src/etl/transformers/base.py`) providing:

| Utility | Description |
|---------|-------------|
| `apply_field_map()` | Generic YAML field_map → DataFrame column loop |
| `grade_to_ceds()` | BC grade → CEDS standard (K→KG, 1→01 …) |
| `map_role()` | Teaching flag (Y/N) → "teacher" / "administrator" |
| `normalize_columns()` | Lowercase + strip DataFrame column names |
| `clean_invalid_ids()` | Drop rows where ID is NaN / empty / "nan" |
| `get_source_file()` | Fetch a named source DataFrame from TransformContext |
| `resolve_date()` | Return start/end date from config or academic year |
| `generate_class_id()` | Build class ID with optional year suffix |
| `generate_class_name()` | Compose "Teacher - Course (section) year" string |
| `determine_school_year()` | Infer school year from data or current date |

### TransformContext

`src/etl/transformers/context.py` — dataclass passed to every `transform()` call:

```python
@dataclass
class TransformContext:
    raw_data: dict[str, pd.DataFrame]   # all loaded source files
    school_year: int                     # e.g. 2025
    academic_start: str                  # e.g. "2025-08-25"
    academic_end: str                    # e.g. "2026-07-25"
    students_output: pd.DataFrame | None # populated after Students runs
```

`students_output` is set after the Students transformer completes and used by later transformers (e.g., Enrollments filters to only active students).

### Entity transformers

| File | Key logic |
|------|-----------|
| `students.py` | Active-only filter, CEDS grade mapping, email generation |
| `staff.py` | Roster join, role mapping (teacher/administrator) |
| `family.py` | Emergency contact extraction, deduplication by student |
| `classes.py` | Homeroom generation, subject class join, blended class integration |
| `enrollments.py` | Student + teacher rows from schedule + demographic data |
| `blended.py` | `BlendedClassDetector` — same teacher/time with 2+ grade levels merged into one class. Falls back to deduplicated schedule for non-enhanced ClassInfo. See [How Classes Work](../partner/how-classes-work.md). |

### Facade

`src/etl/transformer.py` is a thin facade over `TransformContext` and the registry. Existing call sites call `DataTransformer().transform(df, entity_cfg, entity_name, raw_data, global_config)` unchanged.

---

## Loader

**File:** `src/etl/loader.py`

`save_all(outputs, field_orders)` writes all entities atomically:

1. Creates `output_dir/.tmp_<timestamp>/`
2. Writes every entity CSV into the temp dir
3. On success: moves all files into `output_dir/` (overwrites)
4. On any failure: deletes temp dir, raises; previous `output_dir/` files untouched

This prevents a partial-write failure from leaving a mix of old and new files.

---

## Config system

### YAML mapping files

All field mappings live in `config/mappings/<sis_type>_mapping.yaml`. The `--sis` argument selects which file to load.

**Supported field mapping types:**

| Type | Example YAML | Description |
|------|-------------|-------------|
| Direct | `"student number"` | Copy column by name |
| Transform | `{column: grade, transform: grade_to_ceds}` | Apply a named method from BaseTransformer |
| Fixed value | `{value: "active"}` | Same string for every row |
| Academic year date | `{use_academic_year: true}` | Resolved from school year + config |
| ID with year | `{append_year_to_id: true, column: "master timetable id"}` | `<id>_<year>` |
| Email format | `{format: "{studentnumber}@district.ca"}` | Python str.format() with row as kwargs |
| Name position | `{name_position: first}` | Extract first/last from a full-name field |
| ID-role pair | `{id_column: ..., role_column: ..., role_value: ...}` | Used in enrollment rows |

### Pydantic validation

`src/config/models.py` validates every YAML at startup. `MappingConfig` holds `EntityConfig` objects for each entity and a `GlobalConfig`. The `classify_field()` function detects which of the 8 types a field mapping is.

`MappingConfig.to_raw_dict()` converts the validated model back to the plain-dict format the transformer pipeline expects — no YAML re-read needed.

### District inheritance

District configs use `_base: myedbc` to inherit from the standard mapping and only override what differs:

```yaml
# config/mappings/sd48myedbc_mapping.yaml
_base: myedbc
mappings:
  Students:
    source_files:
      student_demographic: "StudentDemographicEnhanced.txt"
```

`_resolve_inheritance()` in `src/config/loader.py` deep-merges the base into the override, with cycle detection.

---

## Supporting modules

| Module | Purpose |
|--------|---------|
| `src/config/app_config.py` | Runtime config (`config.json` in the per-user app-data dir) — SFTP host, schedule, paths, `setup_completed` |
| `src/history/store.py` | SQLite run store (`history.db`) — sole run-record writer/reader; source of Run History (replaces the retired log parser) |
| `src/sftp/uploader.py` | `SFTPUploader` — paramiko SFTP + OS keyring credential retrieval |
| `src/scheduler/windows.py` | Windows Task Scheduler: PowerShell `Register-ScheduledTask` register + tri-state `read_schedule` read-back + `delete_task`; per-op elevation via `elevation.py` |
| `src/scheduler/elevation.py` | Windows per-operation elevation IPC primitive (UAC `runas`, DPAPI handshake) so the app itself never runs elevated |
| `src/scheduler/linux.py` | `crontab` wrapper for Linux cron |
| `src/quality/report.py` | `DataQualityReport` — missing fields, duplicates, orphaned enrollments |
| `src/utils/helpers.py` | `normalize_columns()` and other shared utilities |
| `src/utils/logger.py` | Configured from `config/logging.conf`; log rotates at 5 MB |
| `src/utils/validators.py` | Input validation, SFTP host allowlist enforcement |
| `src/etl/column_names.py` | Column name constants — avoids magic strings across transformers |

---

## Desktop UI (Flet)

The UI is a **native desktop app** built with [Flet](https://flet.dev) — no browser, no HTTP server. `python -m src.main` with no CLI arguments opens the app; passing `--sis`/`--input`/`--output` (etc.) runs the CLI headlessly instead (same process, same exit-code contract). The no-argv branch in `src/main.py` dispatches to `src/ui_flet/launcher.py`, which builds and runs the Flet window.

All UI code lives under `src/ui_flet/`:

| Module | Purpose |
|--------|---------|
| `launcher.py` | Entry point — builds the Flet `Page` and starts the app |
| `shell.py` | App shell — hosts the nav rail and routes to the active screen |
| `nav_rail.py` / `nav.py` | Navigation rail widget + route definitions |
| `components.py` | Shared Flet UI components/widgets |
| `tokens.py` / `theme.py` | Design tokens and theme (colors, spacing, typography) |
| `verdict.py`, `humanize.py`, `home_status.py`, `schedule_status.py`, `schedule_probe.py`, `convert_result.py`, `convert_output.py`, `run_history.py`, `mapping_catalog.py`, `setup_errors.py`, `setup_gates.py`, `sftp_copy.py`, `setup_flow.py`, `job_runner.py` | Pure logic modules (no Flet imports) backing each screen — kept separate from rendering for testability (run records are read from `src/history/store.py`, the SQLite run store; `schedule_status`/`schedule_probe` own the tri-state schedule read-back; `setup_flow` is the wizard state machine) |

Six surfaces, one per module under `src/ui_flet/screens/`:

| Screen | File | Description |
|--------|------|-------------|
| Home | `screens/home.py` | Config health/status dashboard, navigation |
| Setup | `screens/setup.py` | First-run 5-step wizard (District → Folders → Delivery → Schedule → finish; Schedule/Delivery skippable) that graduates into a flat Settings page with one reconciling Save. The Schedule step also carries the opt-in **seasonal pause** (MM-DD window, pre-filled from the district calendar) — config only, never re-registers the task; the gate itself lives in `etl/sync_window.py` + `main._cli` |
| Convert | `screens/convert.py` | Ad-hoc conversion — pick files, convert, view result (resolved-output caption + Open folder), upload via SFTP |
| Run History | `screens/run_history.py` | Reads run records from the SQLite store (`src/history/store.py`) into tabular history |
| Mapping | `screens/mapping.py` | Review the active district config and switch between pre-built configs — **not** a full YAML editor |
| Help | `screens/help.py` | In-app documentation and support links |

`screens/onboarding.py` covers first-run onboarding. The old Streamlit `src/ui/` directory (and the `streamlit` dependency) has been removed entirely.

---

## Security

- **SFTP allowlist** — `src/utils/validators.py` enforces that SFTP uploads only go to known SpacesEDU hosts (`sftp.ca.spacesedu.com`, `sftp.app.spacesedu.com`, `sftp.myblueprint.ca`). Any other host is rejected before a connection is attempted.
- **Input validation** — file paths and config values are validated at startup by Pydantic models and `src/utils/validators.py`.
- **Transform allowlist** — only transforms registered in `BaseTransformer.ALLOWED_TRANSFORMS` can be referenced from YAML configs, preventing arbitrary code execution via config files.
