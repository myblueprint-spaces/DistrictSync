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

- `src/etl/transformers/base.py` — `BaseTransformer` ABC with shared utilities: CEDS grade mapping, `ALLOWED_TRANSFORMS` allowlist, `apply_field_map()` (row-resilient + fail-loud: a bad row blanks only its cell, errors → `context.data_errors`, never silent), `assign_class_ids()`, excluded-course filters, the active-student predicate, and `filter_to_active()` (zero-orphan roster filter for Classes + Enrollments).
- `src/etl/transformers/context.py` — `TransformContext` dataclass: mutable shared state for one pipeline run (school year, academic dates, raw data frames, `active_student_ids` roster published by Students, homeroom/blended maps, and `data_errors` — the per-run fail-loud field-transform ledger surfaced by the pipeline) passed between entity transformers.
- `src/etl/transformers/registry.py` — `TRANSFORMER_REGISTRY` dict and `get_transformer()`: maps entity names to singleton transformer instances; falls back to `DefaultTransformer` (field-map-only) for any unregistered entity.
- `src/etl/transformers/students.py` — `StudentTransformer`: filters to active students (Active + PreReg by default; `active_values` configurable) via the shared `BaseTransformer` predicate (status wins; withdraw-date is a fallback); publishes the active roster to `context.active_student_ids` (zero-orphan); generates emails; normalises Date of Birth to ISO.
- `src/etl/transformers/staff.py` — `StaffTransformer`: optionally merges staff info with a roster file to resolve `staff sourceid`, then applies field map with `map_role` transform (Y → teacher, else → administrator).
- `src/etl/transformers/family.py` — `FamilyTransformer`: thin field-map-only transformer for parent/guardian emergency-contact GDE rows.
- `src/etl/transformers/classes.py` — `ClassTransformer`: orchestrates blended detection, homeroom class generation (configured grades, filtered to `context.active_student_ids` so no all-inactive homeroom class is created), subject class generation (schedule + course + staff join), and emits blended classes from context; deduplicates on Class ID.
- `src/etl/transformers/enrollments.py` — `EnrollmentTransformer`: builds homeroom enrollments (from student demographic) and subject enrollments (from schedule) — both with *student* rows filtered to `context.active_student_ids` (zero-orphan invariant; teacher rows derive from the unfiltered frames) — plus co-teacher/blended teacher enrollments (from class info context); deduplicates on (Class ID, User ID, Role).
- `src/etl/transformers/blended.py` — `BlendedClassDetector`: identifies same-teacher/same-time-slot sections with 2+ grade levels; populates `context.blended_class_map`, `blended_class_metadata`, and `blended_teacher_map`; falls back to deduplicated schedule when ClassInfo is absent.
- `src/etl/transformers/course_info.py` — `CourseInfoTransformer`: filters course rows by `excluded_course_code_patterns` regex list, applies field map, deduplicates on (Course Code, School ID); produces the myBlueprint+ CourseInfo CSV.
- `src/etl/transformers/student_courses.py` — `StudentCoursesTransformer`: ports SD62 PowerShell history/selection join logic; two-pass (history then selection), section-stripping + flavor-truncation course-code cleaning, two-tier CourseInfo lookup; produces the myBlueprint+ StudentCourses CSV.
- `src/etl/transformers/student_attendance.py` — `StudentAttendanceTransformer`: opt-in SpacesEDU feed unioning two optional bands resolved BY `source_files` role (`daily_absences`/`period_absences`), order-independent (daily/period/both), each band's config required only when present — K-7 Daily derives category+rows (fail-loud on unmapped code), 8-12 Period passes through per-period; 4-col `StudentAttendance.csv` (required only), no dedup.

---

## src/config/

- `src/config/models.py` — Pydantic v2 models for YAML mapping validation: `MappingConfig`, `GlobalConfig`, `EntityConfig`, and the discriminated field-mapping variants (transform, fixed value, academic year, append year, email format, name config, id-role pair, enroll-status [strict, `extra="forbid"`], plus bare `str`/`null`); `classify_field()` dispatcher; `to_raw_dict()`/`get_raw_field_map()` for pipeline consumption.
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
- `src/utils/paths.py` — Single source of truth for path resolution: `bundle_root()`, `bundle_config_dir()`, `bundle_mappings_dir()`, `user_data_dir()`, `user_mappings_dir()`, `user_log_file()`; works identically in source-install and frozen-exe (PyInstaller `_MEIPASS`) environments.

---

## src/ui/

- `src/ui/Home.py` — Streamlit multi-page app entry point (`streamlit run src/ui/Home.py`): renders status dashboard (configured/unconfigured banner, last Windows scheduled-task run, SFTP status); auto-discovers pages/ subdirectory.
- `src/ui/brand.py` — myBlueprint/SpacesEDU brand styles: `inject_brand_css()` injects corporate colour palette and card styles; `header()` renders the branded page heading with wordmark; `step_progress()` renders a numbered step bar.
- `src/ui/folder_picker.py` — `pick_directory()`: native OS folder-selection dialog (tkinter `askdirectory`, lazily imported) for the local Setup Wizard's path inputs; returns `None` on cancel/no-GUI so callers fall back to manual text entry.
- `src/ui/launcher.py` — PyInstaller UI launcher: locates `src/ui/Home.py` inside the frozen bundle and invokes Streamlit programmatically with `--server.headless=false`; used when the binary is launched without CLI arguments.
- `src/ui/mapping_helpers.py` — Mapping Editor support library: `detect_columns()` (headerless heuristic), `get_field_metadata()` (field descriptions/types), `build_override_dict()` (diff vs base config), `save_mapping_yaml()`, `column_selectbox()` widget, `SOURCE_FILE_ROLES` and `CEDS_GRADES` constants.

### src/ui/pages/

- `src/ui/pages/01_Setup_Wizard.py` — 5-step setup wizard: file paths → district config → schedule time (Windows collects account password + run-as user for `register_task`; blank warns logged-on-only) → SFTP config (Step 4 verifies credential via `get_stored_password()`) → summary/activation; dashboard. `_classify_schedule_error(msg, elevated)` maps a clean failure to an elevation-aware message (not-elevated → admin; elevated → batch-logon).
- `src/ui/pages/02_Convert.py` — Ad-hoc conversion page: a thin adapter over the shared ETL engine (uploaded GDE bytes → `load_from_bytes` → `run_transform` → `DataLoader`, so download/zip + SFTP use `csv_encoding` and match the CLI byte-for-byte); renders quality report + diff, offers ZIP download / optional SFTP upload.
- `src/ui/pages/03_Run_History.py` — Run History page: parses `__DISTRICTSYNC_RUN__` JSON log lines from `~/.districtsync/etl_tool.log` into a table (raw-tail fallback). The display-only Status cell shows amber "ETL OK · SFTP FAILED" on delivery failure and "Completed with N data errors" on field-transform errors, so the headline never contradicts the exit code.
- `src/ui/pages/04_Mapping_Editor.py` — 7-step visual Mapping Editor: guides non-technical users through entity selection, file upload + column detection, field mapping, academic calendar, and name/email config; saves a minimal `_base`-inheriting override YAML to `~/.districtsync/mappings/`.
- `src/ui/pages/05_Help.py` — Help page: renders `docs/` markdown files (partner guides + developer docs) in the Streamlit UI; single source of truth shared with the MkDocs static site.

---

## config/mappings/

- `config/mappings/myedbc_mapping.yaml` — Base config (v1.9): defines all 7 entity templates (Students, Staff, Family, Classes, Enrollments, CourseInfo, StudentCourses); `enabled_entities` defaults to the 5 standard rostering CSVs; sets homeroom grades, school-year source, and course-code exclusion patterns.
- `config/mappings/sd40myedbc_mapping.yaml` — SD40 New Westminster override (`_base: myedbc`): CSV source file names, headerless schedule with injected column headers, `{student number}@newwestschools.ca` email, `excluded_course_codes` for ATT--AM/PM/Daily attendance rows.
- `config/mappings/sd48myedbc_mapping.yaml` — SD48 Sea to Sky override (`_base: myedbc`): remaps to `StudentDemographicEnhanced.txt` and `StaffInformation.txt`; no other deviations from base.
- `config/mappings/sd51myedbc_mapping.yaml` — SD51 Boundary override (`_base: myedbc`): `StudentDemographicEnhanced.txt`, `{student number}@sd51.bc.ca` email, fixed hardcoded academic start/end dates (bypasses auto-detection).
- `config/mappings/sd54myedbc_mapping.yaml` — SD54 Bulkley Valley override (`_base: myedbc`): lowercase source file names (studentschedule, courseinformation, staffinformation, classinformationenhanced .txt), non-Enhanced staffinformation for Staff, EmergencyContactInformationEnhanced for Family, `{legal surname}.{usual first name}@sd54.bc.ca` email, ATT--AM/PM/Daily excluded; academic dates auto-derive from School Year.
- `config/mappings/sd74myedbc_mapping.yaml` — SD74 Gold Trail override (`_base: myedbc`): swapped legal/usual name fields, `{student number}@sd74.bc.ca` email, `studentcourseselection.txt` as schedule source, `ClassInfoEnhanced.txt`, `ParentInformation.txt`, fixed academic dates.
- `config/mappings/mbp_all_mapping.yaml` — myBlueprint+ full tier (`_base: myedbc`): extends `enabled_entities` to all 7 (adds CourseInfo + StudentCourses on top of the standard 5 rostering CSVs).
- `config/mappings/mbp_core_mapping.yaml` — myBlueprint+ minimal tier (`_base: myedbc`): `enabled_entities` = [Students, CourseInfo, StudentCourses] only; for districts that need course history/selection but not full class rosters.
- `config/mappings/mbponly_mapping.yaml` — myBlueprint+ courses-only tier (`_base: myedbc`): `enabled_entities` = [CourseInfo, StudentCourses] only (no Students); requires only CourseInformation.txt + StudentCourseHistory.txt + StudentCourseSelection.txt.
- `config/mappings/sd51attendance_mapping.yaml` — SD51 attendance-only tier (`_base: sd51myedbc`): `enabled_entities` = [StudentAttendance] only; generates just `StudentAttendance.csv` from the two absence GDEs, independent of the rostering pipeline (no rostering GDEs needed).

---

## Root

- `pyproject.toml` — Project metadata (name=districtsync, version=3.2.0), setuptools build config, pytest settings (addopts, benchmarks deselected, coverage omits), ruff lint/format rules, mypy config, bandit exclusions.
- `Makefile` — Developer shortcuts: `install`, `test`, `test-cov`, `lint`, `fmt`, `ui`, `build-win`, `clean`, `validate-config`, `docs`, `docs-serve`.
- `requirements.txt` — Runtime dependencies: pandas, PyYAML, python-dateutil, pydantic, paramiko, keyring, streamlit.
- `requirements-dev.txt` — Dev/CI dependencies: extends requirements.txt with pytest, pytest-cov, ruff, mypy, bandit, types-paramiko, types-PyYAML, hypothesis, pytest-benchmark, and optional UI-test extras (playwright, pytest-sftpserver).
- `mkdocs.yml` — MkDocs configuration: site name, GitHub repo URL, navigation structure (partner guides + developer docs), Material theme, auto-deploy to GitHub Pages on release tag.
- `README.md` — Project overview, quick-start instructions, supported districts, and links to full documentation.
- `CHANGELOG.md` — Keep-a-Changelog release history; per-release behavior changes (GitHub Releases holds download links + auto-generated commit notes).

---

## tests/

- `tests/conftest.py` — Shared fixtures (synthetic DataFrames, YAML configs, `DataTransformer` instances) for all tests; also hosts the `streamlit_server` session fixture for UI smoke tests.
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
- `tests/test_paths.py` — `src/utils/paths.py` path helpers under both source-install and frozen-bundle (`sys.frozen`) scenarios.
- `tests/test_benchmarks.py` — Performance benchmarks on a synthetic 5 000-student dataset (deselected from normal run; invoke with `-m benchmark`).
- `tests/test_property_based.py` — Hypothesis property-based tests: invariants on grade mapping, email generation, and other pure functions to catch edge cases hand-written tests miss.
- `tests/test_ui_smoke.py` — Playwright headless Chrome smoke tests: each Streamlit page loads without crashing and renders key structural elements; requires the `streamlit_server` fixture.

---

## docs/

- `docs/index.md` — MkDocs home page: hero section with SpacesEDU branding, product summary, and quick-links to partner/developer guides.
- `docs/partner/installation.md` — Partner installation guide: prerequisites, download, Setup Wizard walkthrough (~15–20 min), Windows/Linux task-scheduler setup.
- `docs/partner/faq.md` — Frequently asked questions: run frequency, supported districts, file naming, SFTP behaviour, data privacy.
- `docs/partner/troubleshooting.md` — Troubleshooting guide: no-output causes, encoding errors, SFTP failures, schedule not firing, log file locations.
- `docs/partner/how-classes-work.md` — Explains the three class types (homeroom, subject, blended) and how each is detected from GDE data.
- `docs/partner/headless-sftp-setup.md` — Headless / Docker SFTP setup: configuring SFTP credentials entirely from the CLI (`--sftp-configure`, `--sftp-test`, `--sftp-show`) without a browser.
- `docs/developer/architecture.md` — Architecture overview: ETL pipeline diagram, extractor/transformer/loader responsibilities, config-driven design, blended class logic.
- `docs/developer/setup.md` — Developer setup: Python version, clone, `pip install`, running tests, linting, type checking, Streamlit UI, PyInstaller build.
- `docs/developer/testing.md` — Testing guide: test categories (unit, e2e, snapshot, property-based, benchmark, UI smoke), coverage requirements, mocking patterns.
- `docs/developer/release.md` — Release process: version bump, tag push, GitHub Actions automated build (3 platform binaries), GitHub Release creation, MkDocs deploy.
- `docs/developer/adding-district.md` — Step-by-step guide for adding a new district YAML config with `_base` inheritance and non-standard file names/column mappings.
- `docs/developer/adding-transformer.md` — Guide for adding a custom entity transformer class and registering it in the registry.
