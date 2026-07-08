# Architecture Tree

> Single-source index of the codebase: every source file with a one-line description. **Read this first to locate relevant files instead of exploring blindly.** Keep it current — adding/moving/removing a file requires updating this index in the same change (enforced in CI via `make check-tree`).

_Last generated from `main` @ c669404._

---

## Entry point

- `src/main.py` — CLI entry point: parses argparse flags and dispatches to `run_pipeline` or SFTP subcommands (`--sftp-configure`, `--sftp-test`, `--sftp-show`); exits code 3 when SFTP delivery fails (`PipelineResult.sftp_attempted and not sftp_ok`); re-exports pipeline symbols for backward compatibility; PyInstaller `__main__` target.

---

## src/etl/  (ETL pipeline)

- `src/etl/extractor.py` — `DataExtractor`: parses each GDE file (CSV/TXT) through one bytes-based core with multi-encoding fallback (UTF-8 → Latin1 → CP1252), auto-delimiter detection, and unquoted-`Section` malformed-row repair; two public entrypoints share that core — `load_data` (disk) and `load_from_bytes` (in-memory, e.g. browser uploads); normalises column names immediately after load; raises `ExtractionError` on unparse-able content.
- `src/etl/transformer.py` — `DataTransformer` facade: backward-compatible wrapper that delegates to entity-specific transformers via `TransformContext` and `BlendedClassDetector`; exposes school-year / homeroom state properties for direct test access.
- `src/etl/pipeline.py` — Core ETL orchestration. `run_pipeline` runs Extractor → Transformer → Loader + anomaly/diff/quality + JSON run-log + optional SFTP → `PipelineResult` (exit 1 = no usable input, exit 3 = SFTP failure). `run_transform(...) -> TransformOutputs` (`outputs`/`field_orders`/`data_errors`) is the shared transform seam; `compute_anomalies()` single-sources the row-drop check (CLI + Convert).
- `src/etl/loader.py` — `DataLoader`: writes field-ordered CSVs (`utf-8-sig` BOM; plain UTF-8 for `_NO_BOM_ENTITIES`=`StudentAttendance`). `csv_encoding()` + `select_ordered()` single-source the BOM rule + fail-loud column selection. `save_all()` stages to `.tmp_<ts>/`, then `_commit_staged` commits with backup-and-restore atomicity (`.bak_<ts>/` + `os.replace`, rollback on failure); `archive_stale_outputs()` archives non-current CSVs.
- `src/etl/column_names.py` — Canonical string constants for GDE source-column names (e.g. `MASTER_TIMETABLE_ID`, `STAFF_SOURCEID`, `COURSE_CODE`) to avoid magic strings across transformers.

### src/etl/transformers/

- `src/etl/transformers/base.py` — `BaseTransformer` ABC with shared utilities: CEDS grade mapping, `ALLOWED_TRANSFORMS` allowlist, `apply_field_map()` (row-resilient + fail-loud; errors → `context.data_errors`), `assign_class_ids()`, excluded-course filters, `apply_row_filters()` (config-driven AND-combined row inclusion, fail-loud on a missing column), the active-student predicate, and `filter_to_active()` (zero-orphan roster filter).
- `src/etl/transformers/context.py` — `TransformContext` dataclass: mutable shared state for one pipeline run (school year, academic dates, raw data frames, `active_student_ids` roster published by Students, homeroom/blended maps, and `data_errors` — the per-run fail-loud field-transform ledger surfaced by the pipeline) passed between entity transformers.
- `src/etl/transformers/registry.py` — `TRANSFORMER_REGISTRY` dict and `get_transformer()`: maps entity names to singleton transformer instances; falls back to `DefaultTransformer` (field-map-only) for any unregistered entity.
- `src/etl/transformers/students.py` — `StudentTransformer`: filters to active students (Active + PreReg by default; `active_values` configurable) via the shared `BaseTransformer` predicate; opt-in `cross_enrollment` collapse (dedupe duplicate `User ID` rows to the home-school row); publishes the active roster to `context.active_student_ids` (zero-orphan); generates emails; normalises Date of Birth to ISO.
- `src/etl/transformers/staff.py` — `StaffTransformer`: optionally merges staff info with a roster file to resolve `staff sourceid`, then applies field map with `map_role` transform (Y → teacher, else → administrator).
- `src/etl/transformers/family.py` — `FamilyTransformer`: thin field-map transformer for parent/guardian emergency-contact GDE rows; applies config-driven `row_filters` (e.g. SD60 guardians-only) before the field map.
- `src/etl/transformers/classes.py` — `ClassTransformer`: orchestrates blended detection, homeroom class generation (configured grades, filtered to `context.active_student_ids` so no all-inactive homeroom class is created), subject class generation (schedule + course + staff join), and emits blended classes from context; deduplicates on Class ID.
- `src/etl/transformers/enrollments.py` — `EnrollmentTransformer`: builds homeroom enrollments (from student demographic) and subject enrollments (from schedule) — both with *student* rows filtered to `context.active_student_ids` (zero-orphan invariant; teacher rows derive from the unfiltered frames) — plus co-teacher/blended teacher enrollments (from class info context); deduplicates on (Class ID, User ID, Role).
- `src/etl/transformers/blended.py` — `BlendedClassDetector`: identifies same-teacher/same-time-slot sections with 2+ grade levels; populates `context.blended_class_map`, `blended_class_metadata`, and `blended_teacher_map`; falls back to deduplicated schedule when ClassInfo is absent.
- `src/etl/transformers/course_info.py` — `CourseInfoTransformer`: filters course rows by `excluded_course_code_patterns` regex list, applies field map, deduplicates on (Course Code, School ID); produces the myBlueprint+ CourseInfo CSV.
- `src/etl/transformers/student_courses.py` — `StudentCoursesTransformer`: ports SD62 PowerShell history/selection join logic; two-pass (history then selection), section-stripping + flavor-truncation course-code cleaning, two-tier CourseInfo lookup; produces the myBlueprint+ StudentCourses CSV.
- `src/etl/transformers/student_attendance.py` — `StudentAttendanceTransformer`: opt-in SpacesEDU feed unioning two optional bands resolved BY `source_files` role (`daily_absences`/`period_absences`), order-independent (daily/period/both), each band's config required only when present — K-7 Daily derives category+rows (fail-loud on unmapped code), 8-12 Period passes through per-period; 4-col `StudentAttendance.csv` (required only), no dedup.

---

## src/config/

- `src/config/models.py` — Pydantic v2 models for YAML mapping validation: `MappingConfig`, `GlobalConfig` (incl. opt-in `CrossEnrollmentConfig`), `EntityConfig` (incl. `RowFilter` row inclusion), and field-mapping variants (transform, fixed value, academic year, append year, email format, name config, id-role pair, enroll-status, bare `str`/`null`); `classify_field()` dispatcher; `to_raw_dict()`/`get_raw_field_map()` for pipeline consumption.
- `src/config/loader.py` — `load_config(sis_type)`: discovers YAML from user-overrides dir then bundled dir; resolves `_base` inheritance via recursive deep-merge with cycle detection; validates via Pydantic; exposes `available_configs()` for the UI district picker.
- `src/config/app_config.py` — `AppConfig` dataclass: persists non-sensitive runtime settings (paths, SIS type, schedule, SFTP host/port/user) to `~/.districtsync/config.json` with OS-safe permissions; SFTP password is never stored here (keyring only).

---

## src/quality/

- `src/quality/report.py` — `DataQualityReport` / `EntityReport`: checks missing/empty fields (warns at >50% threshold), entity-specific duplicate detection, orphaned enrollments (class or user not found in outputs), and grade distribution anomalies.

---

## src/sftp/

- `src/sftp/uploader.py` — `SFTPUploader`: paramiko SSHClient to an `ALLOWED_SFTP_HOSTS` host; passwords via OS keyring (`KEYRING_SERVICE`); zips the **rostering** CSVs into `districtsync_<sis>_YYYY-MM-DD.zip` and uploads any `StudentAttendance.csv` standalone outside the zip to the same remote dir (SpacesEDU checks it by name); exposes `test_connection()`, `upload_csvs()`, `get_stored_password()`.

---

## src/scheduler/

- `src/scheduler/windows.py` — Windows Task Scheduler: `register_task` runs a FIXED PowerShell `Register-ScheduledTask` script (`_build_register_script()` via `-EncodedCommand`, reads only `$env:DSYNC_*` child env); password → `Password`/`Highest`, no-password → `Interactive`/`Limited` (never S4U); errors de-CLIXML'd (`catch`→`[Console]::Error.WriteLine` + `_clean_ps_stderr()`); `is_elevated()`; `delete_task`/`query_task` on `schtasks.exe`.
- `src/scheduler/linux.py` — Linux/macOS cron integration: `register_cron()` / `delete_cron()` append/remove a sentinel-tagged crontab entry via the system `crontab` command; uses `shlex.quote()` for safe shell escaping.

---

## src/utils/

- `src/utils/validators.py` — Centralised security validators: `ALLOWED_SFTP_HOSTS` allowlist, `validate_sis_type()`, `validate_task_name()`, `validate_run_time()`, `validate_sftp_host()`, `quote_for_shell()`; all user-supplied values flowing into subprocess or SFTP must pass through here.
- `src/utils/logger.py` — `get_logger()`: configures logging from `config/logging.conf` (or falls back to `basicConfig`) writing to the canonical absolute path `~/.districtsync/etl_tool.log` so logs persist across PyInstaller restarts and scheduled-task runs.
- `src/utils/helpers.py` — General-purpose utilities: `normalize_columns()`, `ensure_directory()`, `validate_csv()`, `validate_path()`, `safe_float_conversion()`, `district_slug()`, `build_zip_name()`.
- `src/utils/paths.py` — Single source of truth for path resolution: `bundle_root()`, `bundle_config_dir()`, `bundle_mappings_dir()`, `app_icon_path()` (shipped brand `.ico`, bundle-relative), `user_data_dir()`, `user_mappings_dir()`, `user_log_file()`; works identically in source-install and frozen-exe (PyInstaller `_MEIPASS`) environments.
- `src/utils/version.py` — `app_version()`: single source of truth for the installed package version (`importlib.metadata.version("districtsync")`, `"dev"` fallback when not packaged); used by the Flet UI (`main.py:196-199`'s inline copy is a tracked ROADMAP DRY follow-up).

---

## src/ui_flet/  (native Flet 1.0 desktop UI — the only UI)

- `src/ui_flet/tokens.py` — Pure brand tokens (no flet import): the 8 `MB_*` brand-colour primitives (the single hex source) + semantic aliases (`color_action_primary`, `color_status_*`, `color_surface`, `color_text`, `color_muted`, `page_bg`) + a WCAG `contrast_ratio()` helper and `UI_CONTRAST_PAIRS` (every fg/bg pair the shell paints, gated >= 4.5:1).
- `src/ui_flet/theme.py` — `build_theme()` / `build_color_scheme()`: maps the semantic tokens onto a Material-3 `ft.ColorScheme` (light-only); the single place the brand→M3 role mapping is decided.
- `src/ui_flet/nav.py` — Pure nav-state model (no flet import): `Destination`/`DESTINATIONS` (ONE fixed order in every state — D7) + `needs_setup(AppConfig)` (single-sourced `not (is_complete() and schedule_registered)`) + `nav_model` + helpers `ordered_destinations` (fixed order) / `prominent_initial_id` (launch on Setup while `needs_setup`, else Home) / `selected_index_for` (single-source rail-index mapping) — rendered by `nav_rail`.
- `src/ui_flet/verdict.py` — COUNTED pure verdict-mapping spine (no flet import): `Verdict` enum (HEALTHY/WARNING/FAILED) + frozen `VerdictVisual(color, icon, headline, tone)` + total `verdict_visuals(v)`; the single source of what healthy/warning/failed looks and reads like — colour ∈ AA-safe verdict tokens, icon name (resolved in the components view) + tone label are the TESTED non-colour cue. Deriving WHICH verdict from state is IA-3.
- `src/ui_flet/setup_errors.py` — COUNTED pure schedule-error classifier (no flet import): `classify_schedule_error(msg, elevated) -> str` maps a de-CLIXML'd/secret-stripped `register_task` failure + elevation flag to calm plain prose (keyed on PowerShell-not-found / ScheduledTasks-missing / Access-denied); `else` surfaces `msg`. Single source for the schedule-error copy; non-leaking (I2). IA-4a.
- `src/ui_flet/setup_gates.py` — COUNTED pure Setup submit-gate predicates (no flet import): `can_register_schedule(config_complete, run_time)` + `can_save_sftp(host, username, remote_path, password, already_configured)`. Single-sources the schedule/SFTP gates so the disabled-button state and the Enter-to-submit (`on_submit`) handlers agree — Enter can't bypass a gate (Slice 2). Mirrors `filepicker.setup_state` (the folders gate).
- `src/ui_flet/run_log.py` — COUNTED pure `__DISTRICTSYNC_RUN__` parser (no flet import): `TAG` + `read_run_records(log_path=None) -> list[dict] | None` reads the run log (default `paths.user_log_file()`) NEWEST-FIRST — missing→`[]` (no runs), malformed lines skipped, unreadable→`None` (graceful-degradation sentinel). The `[]`-vs-`None` split is load-bearing; reusable by IA-6. IA-3a.
- `src/ui_flet/home_status.py` — COUNTED pure Home status-derivation (no flet): `HomeStatus`/`HomeMetrics`/`FixAction` + `is_stale` + `derive_home_status` + shared precedence (`LatestReason`/`classify_latest_reason`/`verdict_for_reason`). Owns the entity vocabulary (`_ROSTERING_ENTITIES`/`_MYBLUEPRINT_ENTITIES` + SINGLE-SOURCE `ENTITY_LABELS`). Consumes shared `humanize.pluralize`/`friendly_anomaly_detail`. IA-3a/IA-6/IA-8a/IA-9.
- `src/ui_flet/humanize.py` — COUNTED pure trust-copy helpers (no flet), all TOTAL: `friendly_district_name` (SIS id → `district_name`, unknown→raw id); `friendly_timestamp` (ISO → plain phrase); `pluralize`; SINGLE-SOURCE `friendly_anomaly_detail(count, *, variant)` (`AnomalyVariant` HOME/HISTORY/CONVERT — byte-for-byte per surface); `friendly_sftp_reason` (bounded category SFTP-failure reason, NEVER the raw core string). DS-2/IA-3/IA-9.
- `src/ui_flet/job_runner.py` — COUNTED worker-thread frame: `JobState` + `JobStateMachine` (single-flight; `start()` no-op-from-RUNNING is THE double-click guard) + `route(work, *, on_success, on_failure)` — the seam encoding the `SystemExit`-vs-`Exception` asymmetry (`SystemExit` caught FIRST → `on_failure`, never re-raised). `JobRunner.run` is `# pragma: no cover` `page.run_thread`→`page.run_task` glue (worker mutates NO control). IA-5a.
- `src/ui_flet/convert_result.py` — COUNTED pure PII-free Convert-result model (no flet): `ConvertStatus` (DELIVERED/BUILT_NOT_DELIVERED[exit-3]/BUILT_WITH_DATA_ERRORS/NEEDS_ANOMALY_ACK/NO_INPUT/NO_OUTPUT) + frozen `ConvertResult` (counts/booleans/quality — NO DataFrames) + total `summarize -> (Verdict, headline, detail)`; NEVER a raw path/`sis_type`/column. Consumes shared `humanize.pluralize`/`friendly_anomaly_detail` (CONVERT). IA-5a/IA-9.
- `src/ui_flet/run_history.py` — COUNTED pure PII-free Run-History derivation (no flet; 2nd consumer of `read_run_records`+`is_stale`): `HistoryBanner` + `SftpDelivery` + frozen `RunRow` (NO `error` field) + `derive_history_banner(...)` (None→unavailable, []→no-runs, else classify latest via `home_status`, CLEAN+stale→WARNING) + `to_run_row`/`to_run_rows`. NEVER raw `error`/`ANOMALY:`. Consumes shared `humanize` helpers (HISTORY). IA-6/IA-9.
- `src/ui_flet/mapping_catalog.py` — COUNTED pure config-catalog (no flet): frozen PII-free `ConfigSummary` (name/output_labels/source_file_count/loaded_ok) + `summarize_config(sis_type, *, config_dir=None)` (TOTAL — output-CSV labels via the empty-`enabled_entities`-means-all rule through `ENTITY_LABELS`; de-duped file count; load failure→degraded, never the raw error) + `list_configs`. Reuses `available_configs`/`friendly_district_name`. IA-8a.
- `src/ui_flet/components.py` — View glue (coverage-omitted): design-system primitives from `tokens` + `verdict_visuals` — `card`/`hero_gradient`, `primary_button`/`secondary_button`/`text_button`, `metric_tile`, `run_table` (`ft.DataTable`: conditional myBlueprint+ columns, text-first status + AA-safe tint, SFTP glyphs, no Error column; labels from `home_status.ENTITY_LABELS`), `FileChip`, `ErrorCard`, `HealthVerdictBanner`, `build_design_demo`.
- `src/ui_flet/nav_rail.py` — View glue (coverage-omitted): `build_nav(...) -> (view, rail)` — flat `ft.NavigationRail` in the fixed `ordered` order (brand mark + reassurance line above Exit); returns the rail handle so the shell can sync the highlight (D7). No lifecycle/config; selection by `dest.id`; initial index via `nav.selected_index_for`.
- `src/ui_flet/shell.py` — View glue (coverage-omitted): `main(page)` builds the themed window (+ brand `page.window.icon` via `paths.app_icon_path()`), the `dict[id -> factory]`, and the rail (`nav_rail.build_nav`); `select_by_id` syncs `rail.selected_index` for programmatic nav (D7); owns the content host + zero-orphan close via the shared async `_close_window` (awaits coroutine `window.destroy()`, `os._exit(0)` fallback).
- `src/ui_flet/launcher.py` — View glue (coverage-omitted): `main()` does frozen-cwd (`resolve_frozen_cwd`) then `ft.run(shell.main)` wrapped in an early-failure path — full traceback to the ETL log sink, a plain-language error dialog/tkinter/stderr fallback, non-zero exit; pure helpers `resolve_log_path`/`format_user_error` are tested.
- `src/ui_flet/filepicker.py` — COUNTED boundary logic: `ft.FilePicker` async-service wrapper (`pick_directory`/`pick_files`, registered once via the tested idempotent `_ensure_picker`; only `await`-dialog glue is `# pragma: no cover`) + pure validation mirroring `run_pipeline` (`validate_input_dir` exists+is_dir, `validate_output_dir` parent-structural, effectful `check_writable` w/ TOCTOU note) + the pure `setup_state` save-gate.
- `src/ui_flet/picker_field.py` — View glue (coverage-omitted): `PickerField` (themed `ft.Column`) — label + "Browse…" button (`components.primary_button`) calling the async `pick_directory` wrapper + chosen-path display + inline valid/invalid line; takes a `validator` + `on_change(path, result)`; the one reusable picker every later surface reuses (no tkinter port).
- `src/ui_flet/screens/__init__.py` — Flet UI surfaces package marker (one module per real navigation surface; trust-critical logic stays in COUNTED pure helpers).
- `src/ui_flet/screens/setup.py` — View glue (coverage-omitted): `build_setup(page)` composes folders (gated Save→`setup_state`) + `_build_schedule_section` (`register_task`/`register_cron`→`classify_schedule_error`) + `_build_sftp_section` (allowlist dropdown + `store_password` keyring; Test-connection off-thread). Enter-to-submit fields fire Register/Save via the pure `setup_gates` predicate so Enter can't bypass the button gate (Slice 2).
- `src/ui_flet/screens/onboarding.py` — View glue (coverage-omitted): `build_onboarding(page, *, sis_type="", on_start_setup)` — the reusable first-run hero (UNCONFIGURED Home, IA branch (a)): branded hero greeting the friendly district name + `HealthVerdictBanner(Verdict.WARNING)` + steps + "Start setup" CTA. Callback-driven (owns NO nav/lifecycle — `on_start_setup` injected); static, no empty state. IA-3 reuses it.
- `src/ui_flet/screens/home.py` — View glue (coverage-omitted): `build_home(page, *, app_config, on_navigate)` — three-way Home dashboard. Branch (a) `nav.needs_setup`→reuses `build_onboarding`; (b)/(c) read `read_run_records`+`derive_home_status`, render verdict-first (`HealthVerdictBanner` + fix-path `primary_button` + `metric_tile` from `HomeMetrics`; labels from `home_status.ENTITY_LABELS`). Sync read; never-crash `ErrorCard`. IA-3b.
- `src/ui_flet/screens/convert.py` — View glue (coverage-omitted): `build_convert(page)` manual convert (`PickerField`+`FileChip`s → `JobRunner.run` → verdict/tiles/quality; SFTP pre-flight → exit-3 `BUILT_NOT_DELIVERED`; tile labels from `home_status.ENTITY_LABELS`) + `convert_job(...)` parity adapter off-thread (`run_transform`→`compute_anomalies`→ack-gated `save_all`→tight `upload_csvs` catch) + `is_write_in_flight()`. IA-5a/b.
- `src/ui_flet/screens/run_history.py` — View glue (coverage-omitted): `build_run_history(page, *, app_config)` — read-only Run History (no `on_navigate`). Sync read (`read_run_records`); verdict-first `derive_history_banner` → `HealthVerdictBanner`, then (non-empty) `run_table(to_run_rows(records)[:50])` in a horizontally-scrollable `ft.Row`. None/[]→banner only; never-crash `ErrorCard`. IA-6.
- `src/ui_flet/screens/help.py` — View glue (coverage-omitted): `build_help(page, *, app_config)` — link-out Help (no bundled-docs render, per 0013 scope-lock). Hero + "Get help" card (`launch_url(HELP_CENTRE_URL)` + `mailto:SUPPORT_EMAIL`, both also offline-readable `ft.Text(selectable=True)`) + decouple-the-sync reassurance. `HELP_CENTRE_URL`/`SUPPORT_EMAIL` drift-guarded constants; never-crash `ErrorCard`. IA-7.
- `src/ui_flet/screens/mapping.py` — View glue (coverage-omitted): `build_mapping(page, *, app_config)` — select-a-pre-built-config review-and-switch surface (NOT the full editor; IA-8b deferred). Current-mapping card + a switch `ft.Dropdown` (`available_configs()` allowlist) showing the pending `mapping_catalog` summary + a gated Apply (`loaded_ok` AND ≠ current) → writes `AppConfig.sis_type` → `HealthVerdictBanner`. IA-8a.

---

## config/mappings/

- `config/mappings/myedbc_mapping.yaml` — Base config (v1.9): defines all 7 entity templates (Students, Staff, Family, Classes, Enrollments, CourseInfo, StudentCourses); `enabled_entities` defaults to the 5 standard rostering CSVs; sets homeroom grades, school-year source, and course-code exclusion patterns.
- `config/mappings/sd40myedbc_mapping.yaml` — SD40 New Westminster override (`_base: myedbc`): CSV source file names, headerless schedule with injected column headers, `{student number}@newwestschools.ca` email, `excluded_course_codes` for ATT--AM/PM/Daily attendance rows.
- `config/mappings/sd48myedbc_mapping.yaml` — SD48 Sea to Sky override (`_base: myedbc`): remaps to `StudentDemographicEnhanced.txt` and `StaffInformation.txt`; no other deviations from base.
- `config/mappings/sd51myedbc_mapping.yaml` — SD51 Boundary override (`_base: myedbc`): `StudentDemographicEnhanced.txt`, `{student number}@sd51.bc.ca` email, fixed hardcoded academic start/end dates (bypasses auto-detection).
- `config/mappings/sd54myedbc_mapping.yaml` — SD54 Bulkley Valley override (`_base: myedbc`): lowercase source file names (studentschedule, courseinformation, staffinformation, classinformationenhanced .txt), non-Enhanced staffinformation for Staff, EmergencyContactInformationEnhanced for Family, `{legal surname}.{usual first name}@sd54.bc.ca` email, ATT--AM/PM/Daily excluded; academic dates auto-derive from School Year.
- `config/mappings/sd60myedbc_mapping.yaml` — SD60 Peace River North override (`_base: myedbc`): `Student_demo_enh.txt`/`StaffInformation.txt`/`EmergencyEnhanced.txt`/`StudentCourseSelection.txt`(schedule)/`ClassInformation.txt`; section→`Section`, title→`Title`; `ATT--AM/PM` excluded; Family `row_filters` guardians-only; `cross_enrollment.collapse` home-school dedupe; `Active No Primary` retained.
- `config/mappings/sd74myedbc_mapping.yaml` — SD74 Gold Trail override (`_base: myedbc`): swapped legal/usual name fields, `{student number}@sd74.bc.ca` email, `studentcourseselection.txt` as schedule source, `ClassInfoEnhanced.txt`, `ParentInformation.txt`, fixed academic dates.
- `config/mappings/mbp_all_mapping.yaml` — myBlueprint+ full tier (`_base: myedbc`): extends `enabled_entities` to all 7 (adds CourseInfo + StudentCourses on top of the standard 5 rostering CSVs).
- `config/mappings/mbp_core_mapping.yaml` — myBlueprint+ minimal tier (`_base: myedbc`): `enabled_entities` = [Students, CourseInfo, StudentCourses] only; for districts that need course history/selection but not full class rosters.
- `config/mappings/mbponly_mapping.yaml` — myBlueprint+ courses-only tier (`_base: myedbc`): `enabled_entities` = [CourseInfo, StudentCourses] only (no Students); requires only CourseInformation.txt + StudentCourseHistory.txt + StudentCourseSelection.txt.
- `config/mappings/sd51attendance_mapping.yaml` — SD51 attendance-only tier (`_base: sd51myedbc`): `enabled_entities` = [StudentAttendance] only; generates just `StudentAttendance.csv` from the two absence GDEs, independent of the rostering pipeline (no rostering GDEs needed).

---

## Root

- `pyproject.toml` — Project metadata (name=districtsync, version=3.2.0), setuptools build config, pytest settings (addopts, benchmarks deselected, coverage omits), ruff lint/format rules, mypy config, bandit exclusions.
- `Makefile` — Developer shortcuts: `install`, `test`, `test-cov`, `lint`, `fmt`, `ui`, `build-win`, `build-flet-win`, `clean`, `validate-config`.
- `requirements.txt` — Runtime dependencies: pandas, PyYAML, python-dateutil, pydantic, paramiko, keyring, flet, flet-desktop.
- `requirements-dev.txt` — Dev/CI dependencies: extends requirements.txt with pytest, pytest-cov, ruff, mypy, bandit, types-paramiko, types-PyYAML, hypothesis, pytest-benchmark, and optional UI-test extras (playwright, pytest-sftpserver).
- `README.md` — Project overview, quick-start instructions, supported districts, and links to full documentation.
- `CHANGELOG.md` — Keep-a-Changelog release history; per-release behavior changes (GitHub Releases holds download links + auto-generated commit notes).

---

## tests/

- `tests/conftest.py` — Shared fixtures (synthetic DataFrames, YAML configs, `DataTransformer` instances) for all tests.
- `tests/snapshots/generate_synthetic.py` — Script to regenerate synthetic SD74 GDE input files in `tests/snapshots/input/` (run once after schema changes).
- `tests/snapshots/` — Frozen SD74 snapshot data: `input/` holds 6 synthetic GDE files (StudentDemographic, Staff, Family, Classes, Schedule, CourseInfo); `output/` holds 5 golden CSV files (Students, Staff, Family, Classes, Enrollments) locked against regression.
- `tests/snapshots/mbp_input/` — Small hand-authored synthetic GDEs for the `mbponly` course tier (CourseInformation, StudentCourseHistory, StudentCourseSelection); consumed by the mbponly end-to-end pipeline test.
- `tests/test_config.py` — Config model and loader: Pydantic validation of YAML structure, `classify_field()` dispatch, `_base` inheritance deep-merge, cycle detection.
- `tests/test_config_loader_multi_dir.py` — Two-tier config discovery: user-dir override wins over bundled, `_base` resolution across search dirs, `available_configs()` deduplication.
- `tests/test_pipeline_e2e.py` — Full ETL e2e with synthetic on-disk GDE files: verifies output CSV structure and data for the standard myedbc config.
- `tests/test_pipeline_e2e_districts.py` — District-specific e2e: verifies sd48 and sd74 district configs produce all 5 expected CSVs from synthetic GDE files using district-specific filenames.
- `tests/test_pipeline_e2e_mbponly.py` — `mbponly` tier e2e smoke test: runs the pipeline against `tests/snapshots/mbp_input/` and asserts it emits only CourseInfo.csv + StudentCourses.csv (no rostering CSVs) with the right schema and required-files set.
- `tests/test_regression_sd74.py` — SD74 golden-file regression: runs the pipeline against `tests/snapshots/input/` and diffs against `tests/snapshots/output/` (schema + values).
- `tests/test_contract.py` — Output schema contract: asserts every district config produces exactly the required SpacesEDU Advanced CSV column set — no missing columns, no unexpected extras.
- `tests/test_transform_students.py` — Students transformer: enrollment-status filtering, active-student logic, email generation, Date of Birth normalisation.
- `tests/test_transform_staff.py` — Staff transformer: field mapping, roster merge for `staff sourceid`, role mapping.
- `tests/test_transform_family.py` — Family transformer: field mapping from emergency-contact GDE rows.
- `tests/test_transform_classes.py` — Classes transformer: homeroom generation, subject class creation, blended class integration.
- `tests/test_transform_enrollments.py` — Enrollments transformer: homeroom/subject/co-teacher enrollments, deduplication on (Class ID, User ID, Role).
- `tests/test_transform_course_info.py` — CourseInfo transformer: course-code pattern exclusion, deduplication on (Course Code, School ID); uses synthetic MyEd BC CourseInformation data.
- `tests/test_transform_student_courses.py` — StudentCourses transformer: history/selection join logic, W-mark skipping, section-stripping, flavor truncation, CourseInfo two-tier lookup.
- `tests/test_transform_base.py` — BaseTransformer shared utilities: `filter_excluded_course_code_patterns()` and `clean_course_code_flavor()` (other helpers are covered indirectly by entity tests).
- `tests/test_blended_classes.py` — Blended class detection: detection correctness, naming convention, grade-range merging, validation.
- `tests/test_class_generation.py` — Class ID and class name generation, 100-char name truncation.
- `tests/test_grade_mapping.py` — CEDS grade-code mapping (`grade_to_ceds`), edge cases and unknown grades.
- `tests/test_email_generation.py` — Student email template rendering (`format` field type), various template patterns.
- `tests/test_enrollment_status.py` — Enrollment status determination: Active/PreReg/Inactive via `enrolment status` column and 4-format withdrawal-date fallback.
- `tests/test_zero_orphan_enrollments.py` — Zero-orphan invariant: Students publishes the active roster and Classes (homeroom) + Enrollments (homeroom + subject) filter their student rows against it; asserts no student enrollment references a non-rostered `User ID`, teacher/co-teacher rows stay byte-identical, all-inactive homerooms produce no class, and the empty-roster guard leaves rows intact + warns.
- `tests/test_role_mapping.py` — Staff role mapping (Y → teacher, else → administrator) and User ID / User Role pair generation.
- `tests/test_school_year.py` — School year determination from schedule data and calendar-date heuristic; academic start/end date calculation; datetime mock patterns.
- `tests/test_extractor.py` — `DataExtractor` multi-encoding/delimiter fallback, headerless file injection, `ExtractionError` on unparse-able files.
- `tests/test_loader.py` — `DataLoader` CSV output, field ordering, atomic `save_all()` commit/rollback behaviour.
- `tests/test_quality_report.py` — `DataQualityReport` checks: missing fields, duplicates, orphaned enrollments, grade distribution.
- `tests/test_validators.py` — All validators in `src/utils/validators.py`: SIS type, task name, run time, SFTP host allowlist, shell quoting.
- `tests/test_app_config.py` — `AppConfig` load/save round-trip, unknown-field tolerance, default values.
- `tests/test_main_helpers.py` — Pipeline helper functions: `_check_anomalies`, `_emit_run_log`, `extract_required_files`, `_sftp_upload`, `_print_diff`.
- `tests/test_cli.py` — CLI flags: `--dry-run`, `--diff`, `--quality`, `--version` (calls `run_pipeline()` directly, bypasses argparse).
- `tests/test_sftp_uploader.py` — `SFTPUploader` with mocked paramiko and keyring: store/retrieve password, `test_connection()`, `upload_csvs()` zip-and-put flow.
- `tests/test_sftp_cli.py` — SFTP CLI subcommands: `--sftp-configure` (env var + stdin password sources), `--sftp-test`, `--sftp-show`, host allowlist rejection, flag mutual-exclusion.
- `tests/test_sftp_integration.py` — Live SFTP integration using `pytest-sftpserver` (real paramiko transport); skipped automatically if the package is absent.
- `tests/test_schedulers.py` — Windows Task Scheduler and Linux cron wrappers with all subprocess calls mocked.
- `tests/test_scheduler_runas.py` — Unit tests for `register_task` run-as behavior: asserts `/RU /RP /RL HIGHEST` flags when a password is supplied, omitted when not (back-compat), password never appears in captured logs (only `***`), and `validate_run_as_user` accepts/rejects correctly.
- `tests/test_sftp_exit.py` — CLI exit-code tests for SFTP failure path: asserts exit 3 when SFTP is attempted and fails (with output CSVs still present on disk), exit 0 on success or when `--sftp` is absent, and exit 0 on `--dry-run --sftp` (no upload attempted).
- `tests/test_registry.py` — Transformer registry: known entity lookup, `DefaultTransformer` fallback for unregistered entities.
- `tests/test_source_config.py` — Source-config normalisation (`normalize_source_config`) and `get_source_file()` retrieval from context.
- `tests/test_helpers.py` — `src/utils/helpers.py` utilities: `normalize_columns()`, `ensure_directory()`, `district_slug()`, `build_zip_name()`, etc.
- `tests/test_paths.py` — `src/utils/paths.py` path helpers under both source-install and frozen-bundle (`sys.frozen`) scenarios, incl. `app_icon_path()` (dev tree vs `_MEIPASS`; committed `.ico` present).
- `tests/test_ui_flet_setup_gates.py` — `src/ui_flet/setup_gates.py` pure submit-gate predicates (`can_register_schedule` / `can_save_sftp`) truth tables — the Enter-can't-bypass-the-button guarantee (Slice 2).
- `tests/test_ui_flet_shell_exit.py` — `src/ui_flet/shell._close_window` awaits the coroutine `window.destroy()` (regression for the un-awaited-no-op Exit bug) + `os._exit(0)` fallback (Slice 2).
- `tests/test_benchmarks.py` — Performance benchmarks on a synthetic 5 000-student dataset (deselected from normal run; invoke with `-m benchmark`).
- `tests/test_property_based.py` — Hypothesis property-based tests: invariants on grade mapping, email generation, and other pure functions to catch edge cases hand-written tests miss.

---

## docs/

- `docs/index.md` — Documentation home page: hero section with SpacesEDU branding, product summary, and quick-links to partner/developer guides.
- `docs/partner/installation.md` — Partner installation guide: prerequisites, download, Setup Wizard walkthrough (~15–20 min), Windows/Linux task-scheduler setup.
- `docs/partner/faq.md` — Frequently asked questions: run frequency, supported districts, file naming, SFTP behaviour, data privacy.
- `docs/partner/troubleshooting.md` — Troubleshooting guide: no-output causes, encoding errors, SFTP failures, schedule not firing, log file locations.
- `docs/partner/how-classes-work.md` — Explains the three class types (homeroom, subject, blended) and how each is detected from GDE data.
- `docs/partner/headless-sftp-setup.md` — Headless / Docker SFTP setup: configuring SFTP credentials entirely from the CLI (`--sftp-configure`, `--sftp-test`, `--sftp-show`) without a browser.
- `docs/developer/architecture.md` — Architecture overview: ETL pipeline diagram, extractor/transformer/loader responsibilities, config-driven design, blended class logic.
- `docs/developer/setup.md` — Developer setup: Python version, clone, `pip install`, running tests, linting, type checking, Flet UI, PyInstaller build.
- `docs/developer/testing.md` — Testing guide: test categories (unit, e2e, snapshot, property-based, benchmark), coverage requirements, mocking patterns.
- `docs/developer/release.md` — Release process: version bump, tag push, GitHub Actions automated build (3 platform binaries), GitHub Release creation.
- `docs/developer/adding-district.md` — Step-by-step guide for adding a new district YAML config with `_base` inheritance and non-standard file names/column mappings.
- `docs/developer/adding-transformer.md` — Guide for adding a custom entity transformer class and registering it in the registry.
