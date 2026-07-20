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
- `src/etl/pipeline.py` — Core ETL orchestration. `run_pipeline(..., source)` = Extractor → Transformer → Loader + anomaly/diff/quality → `PipelineResult` (exit 1 no-input, 3 SFTP). Builds the run record ONCE (`build_run_record`) → the `__DISTRICTSYNC_RUN__` log line + the durable store (`_store_run_record`: non-fatal, post-commit, never masks the ETL error). `_resolve_source` explicit→`DSYNC_SOURCE`→cli; `run_transform`/`compute_anomalies`.
- `src/etl/loader.py` — `DataLoader`: writes field-ordered CSVs (`utf-8-sig` BOM; plain UTF-8 for `_NO_BOM_ENTITIES`=`StudentAttendance`). `csv_encoding()` + `select_ordered()` single-source the BOM rule + fail-loud column selection. `save_all()` stages to `.tmp_<ts>/`, then `_commit_staged` commits with backup-and-restore atomicity (`.bak_<ts>/` + `os.replace`, rollback on failure); `archive_stale_outputs()` archives non-current CSVs.
- `src/etl/column_names.py` — Canonical string constants for GDE source-column names (e.g. `MASTER_TIMETABLE_ID`, `STAFF_SOURCEID`, `COURSE_CODE`) to avoid magic strings across transformers.

### src/etl/transformers/

- `src/etl/transformers/base.py` — `BaseTransformer` ABC: CEDS grade mapping, `ALLOWED_TRANSFORMS` (defensive reference; canonical set lives in config.models), `apply_field_map()` (thin typed dispatch over `ConfiguredField.apply` — row-resilient, fail-loud), `assign_class_ids()`, excluded-course + `apply_row_filters()`, `filter_to_active()` (zero-orphan), `generate_student_email` (opt-in `sanitize`), date helpers (empty-on-unparseable).
- `src/etl/transformers/grades.py` — Grade vocabulary helpers: the CEDS grade table + `grade_to_ceds` + `split_by_homeroom_grades` (the hoisted grade→CEDS→homeroom/subject split shared by Classes and Enrollments).
- `src/etl/transformers/dates.py` — Flexible GDE date parsing/formatting: the SINGLE `INPUT_DATE_FORMATS` grid, friendly-token→strftime translation, withdraw-date classification, pure school-year determination (today is always a parameter — `datetime.now()` stays in base.py, the test seam).
- `src/etl/transformers/course_codes.py` — Course-code exclusion + cleaning: exact-code and regex-pattern row filters, the early-grade floor pattern, effective-patterns composer, SD62-style flavor truncation; shared by Classes/Enrollments/blended/CourseInfo/StudentCourses.
- `src/etl/transformers/emails.py` — Student email template interpolation (`generate_student_email` with opt-in `sanitize`); the derived-dates machinery stays with its consumer `StudentTransformer._generate_emails`.
- `src/etl/transformers/ids.py` — Shared ID/join-key normalization: `normalize_id_series` (the single `astype(str).str.strip()` spelling for every cross-frame join/filter) + `clean_invalid_ids`.
- `src/etl/transformers/naming.py` — Class-name construction: word-boundary `truncate_name` (100-char Advanced CSV limit) + `generate_class_name`; imported by BaseTransformer, BlendedClassDetector, and the DataTransformer facade.
- `src/etl/transformers/sources.py` — `source_files` config normalization (dict / list-of-dicts / legacy list → {role: filename}) + role-based frame access from `TransformContext.raw_data` (always a copy, warn on unresolved role).
- `src/etl/transformers/context.py` — `TransformContext` dataclass: mutable shared state for one pipeline run (school year, academic dates, raw data frames, `active_student_ids` roster published by Students, homeroom/blended maps, and `data_errors` — the per-run fail-loud field-transform ledger surfaced by the pipeline) passed between entity transformers.
- `src/etl/transformers/registry.py` — `TRANSFORMER_REGISTRY` dict and `get_transformer()`: maps entity names to singleton transformer instances; falls back to `DefaultTransformer` (field-map-only) for any unregistered entity.
- `src/etl/transformers/students.py` — `StudentTransformer`: filters to active students (Active + PreReg default; `active_values` configurable); opt-in `cross_enrollment` collapse (dedupe duplicate `User ID` rows to the home-school row); publishes the active roster to `context.active_student_ids` (zero-orphan); generates emails (opt-in `sanitize` + `derived_dates` date-part injection, fail-loud on missing column); normalises DOB to ISO.
- `src/etl/transformers/staff.py` — `StaffTransformer`: optionally merges staff info with a roster file to resolve `staff sourceid`, then applies field map with `map_role` transform (Y → teacher, else → administrator).
- `src/etl/transformers/family.py` — `FamilyTransformer`: thin field-map transformer for parent/guardian emergency-contact GDE rows; applies config-driven `row_filters` (e.g. SD60 guardians-only) before the field map.
- `src/etl/transformers/classes.py` — `ClassTransformer`: orchestrates blended detection, homeroom class generation (configured grades, filtered to `context.active_student_ids` so no all-inactive homeroom class is created), subject class generation (schedule + course + staff join), and emits blended classes from context; deduplicates on Class ID.
- `src/etl/transformers/enrollments.py` — `EnrollmentTransformer`: builds homeroom enrollments (from student demographic) and subject enrollments (from schedule) — both with *student* rows filtered to `context.active_student_ids` (zero-orphan invariant; teacher rows derive from the unfiltered frames) — plus co-teacher/blended teacher enrollments (from class info context); deduplicates on (Class ID, User ID, Role).
- `src/etl/transformers/blended.py` — `BlendedClassDetector`: identifies same-teacher/same-time-slot sections with 2+ grade levels; populates `context.blended_class_map`, `blended_class_metadata`, and `blended_teacher_map`; falls back to deduplicated schedule when ClassInfo is absent.
- `src/etl/transformers/course_info.py` — `CourseInfoTransformer`: filters course rows by `excluded_course_code_patterns` regex list, applies field map, deduplicates on (Course Code, School ID); produces the myBlueprint+ CourseInfo CSV.
- `src/etl/transformers/student_courses.py` — `StudentCoursesTransformer`: ports SD62 PowerShell history/selection join logic; two-pass (history then selection), section-stripping + flavor-truncation course-code cleaning, two-tier CourseInfo lookup; produces the myBlueprint+ StudentCourses CSV.
- `src/etl/transformers/student_attendance.py` — `StudentAttendanceTransformer`: opt-in SpacesEDU feed unioning two optional bands resolved BY `source_files` role (`daily_absences`/`period_absences`), order-independent (daily/period/both), each band's config required only when present — K-7 Daily derives category+rows (fail-loud on unmapped code), 8-12 Period passes through per-period; 4-col `StudentAttendance.csv` (required only), no dedup.

---

## src/history/  (durable run-history store)

- `src/history/__init__.py` — Run-history persistence package marker (UI-neutral + platform-neutral; the pipeline/Convert write, the Flet UI reads; NO `ui_flet` import).
- `src/history/store.py` — SQLite run store (`history.db`; replaces the retired log-parser). `write_run_record` = sole creator/migrator (schema + `user_version=1`, WAL/DELETE fallback, non-fatal, quarantine-recreate on corruption, never downgrades). `read_run_records` never creates the DB (missing/empty→`[]`, error→`None`, else newest-first flat dicts). `store_meta().created_at` = fresh-start signal; `VALID_SOURCES` = source CHECK set.

---

## src/config/

- `src/config/models.py` — Pydantic v2 YAML models + typed field Strategy: `MappingConfig`/`GlobalConfig`/`EntityConfig` (fail-fast `ALLOWED_TRANSFORMS` gate at load; single source), `ConfiguredField` variants with `.apply()`; `classify_field()`/`ensure_field_mapping()` (idempotent normalization); `to_raw_dict()`/`get_raw_field_map()` (deletion deferred — ROADMAP).
- `src/config/loader.py` — `load_config(sis_type)`: discovers YAML from user-overrides dir then bundled dir; resolves `_base` inheritance via recursive deep-merge with cycle detection; validates via Pydantic; exposes `available_configs()` for the UI district picker.
- `src/config/app_config.py` — `AppConfig`: persists non-sensitive settings (paths, SIS type, schedule, SFTP host/port/user, `setup_completed`) to `config.json` via `paths.user_data_dir()`; SFTP password keyring-only. `has_completed_setup()` (D4a) = SINGLE-source durable finish-line (explicit flag OR `is_complete() and schedule_registered`), baked on `load()` so no deployed install regresses into onboarding.

---

## src/quality/

- `src/quality/report.py` — `DataQualityReport` / `EntityReport`: checks missing/empty fields (warns at >50% threshold), entity-specific duplicate detection, orphaned enrollments (class or user not found in outputs), and grade distribution anomalies.

---

## src/sftp/

- `src/sftp/uploader.py` — `SFTPUploader`: paramiko SSHClient to an `ALLOWED_SFTP_HOSTS` host; keyring passwords (`KEYRING_SERVICE`); zips **rostering** CSVs into `districtsync_<sis>_YYYY-MM-DD.zip`, `StudentAttendance.csv` standalone; `test_connection` (auth IS the test — typed pw → `connect()` only, D6; listing-denied → `(True, LISTING_DENIED_NOTE)`, missing path → fail), `upload_csvs()` (fail-loud on empty dir), `get_stored_password()`.

---

## src/scheduler/

- `src/scheduler/__init__.py` — the ONE platform-dispatch point (W4a T2.3): `Scheduler` Protocol + honest asymmetric capability flags, thin `WindowsTaskScheduler`/`CronScheduler` adapters (Windows delete owns the access-denied → elevated retry; cron password FAILS LOUD; cron read-back UNKNOWN-shaped), `get_scheduler()` reading `sys.platform` at call time.
- `src/scheduler/windows.py` — Windows Task Scheduler: `register_task` = FIXED `Register-ScheduledTask` script (`-EncodedCommand`, only `$env:DSYNC_*`, `--source scheduled`, de-CLIXML'd); `is_elevated()`; `read_schedule -> ScheduleReadback` (D4 tri-state, ~10s); `delete_task`. D5: non-elevated `register_task` self-elevates via `elevation` (child bootstrap runs `_register_body`, DPAPI, fail-closed, read-back-confirmed) + `delete_task_elevated`.
- `src/scheduler/elevation.py` — Generic Windows elevation IPC primitive (D5): DPAPI CurrentUser `protect_blob`/`unprotect_blob` (app entropy, never LocalMachine); `write_request`/`read_result`/`new_result_path` (`dsync_elev_*` handshake, owner-only DACL, plaintext atomic result); `run_elevated_powershell` (ShellExecuteExW `runas`, System32 pin, bounded wait → `ElevationOutcome`); `sweep_orphans`.
- `src/scheduler/linux.py` — Linux/macOS cron integration: `register_cron()` / `delete_cron()` append/remove a sentinel-tagged crontab entry via the system `crontab` command; uses `shlex.quote()` for safe shell escaping.

---

## src/utils/

- `src/utils/validators.py` — Centralised security validators: `ALLOWED_SFTP_HOSTS` allowlist, `validate_sis_type()`, `validate_task_name()`, `validate_run_time()`, `validate_sftp_host()`, `quote_for_shell()`; all user-supplied values flowing into subprocess or SFTP must pass through here.
- `src/utils/logger.py` — `get_logger()`: configures logging from `config/logging.conf` (or falls back to `basicConfig`) writing to the canonical `paths.user_log_file()` (the per-OS app-data dir, e.g. `%LOCALAPPDATA%\DistrictSync\etl_tool.log`) so logs persist across PyInstaller restarts and scheduled-task runs.
- `src/utils/helpers.py` — General-purpose utilities: `normalize_columns()`, `ensure_directory()`, `validate_csv()`, `validate_path()`, `safe_float_conversion()`, `district_slug()`, `build_zip_name()`, `subprocess_no_window_flags()` (single-source no-console `creationflags` — every Windows-facing `subprocess.run` must pass it so the windowed exe never flashes a console).
- `src/utils/paths.py` — Single source of truth for path resolution: bundle helpers (`bundle_root`, `app_icon_path`, …), `user_data_dir()` (one app-data seam, call-time via `platformdirs` per-OS + legacy `~/.districtsync` fallback), `migrate_legacy_data_dir()` (idempotent, failure-safe stage-then-atomic-promote relocation + `MOVED.txt` breadcrumb), `user_mappings_dir`/`user_log_file`/`user_history_db`; source + frozen (`_MEIPASS`).
- `src/utils/version.py` — `app_version()`: single source of truth for the installed package version (`importlib.metadata.version("districtsync")`, `"dev"` fallback when not packaged); used by the Flet UI (`main.py:196-199`'s inline copy is a tracked ROADMAP DRY follow-up).

---

## src/ui_flet/  (native Flet 1.0 desktop UI — the only UI)

- `src/ui_flet/tokens.py` — Pure brand tokens (no flet import): 8 `MB_*` colour primitives (single hex source) + semantic aliases + Direction B design-system scales (`space_*`/`radius_*`/`type_*`) + roles (navy rail, `color_content_wash`, toned status tints/lines + deep on-tint text) + WCAG `contrast_ratio()` + `UI_CONTRAST_PAIRS` (every painted fg/bg pair, gated >= 4.5:1). See `docs/DESIGN_SYSTEM.md`.
- `src/ui_flet/theme.py` — `build_theme()` / `build_color_scheme()`: maps the semantic tokens onto a Material-3 `ft.ColorScheme` (light-only); the single place the brand→M3 role mapping is decided.
- `src/ui_flet/nav.py` — Pure nav-state model (no flet): `Destination`/`DESTINATIONS` (ONE fixed order — D7) + `needs_setup(AppConfig)` (re-keyed Slice 5/D4a to `not has_completed_setup()` — the durable finish-line, so a broken-schedule Firefighter isn't onboarded) + `nav_model` + `ordered_destinations`/`prominent_initial_id`/`selected_index_for` (single-source rail-index) — rendered by `nav_rail`.
- `src/ui_flet/verdict.py` — COUNTED pure verdict-mapping spine (no flet import): `Verdict` enum (HEALTHY/WARNING/FAILED) + frozen `VerdictVisual(color, icon, headline, tone)` + total `verdict_visuals(v)`; the single source of what healthy/warning/failed looks and reads like — colour ∈ AA-safe verdict tokens, icon name (resolved in the components view) + tone label are the TESTED non-colour cue. Deriving WHICH verdict from state is IA-3.
- `src/ui_flet/setup_errors.py` — COUNTED pure schedule-error classifier (no flet): `classify_schedule_error(msg, elevated) -> str` maps a de-CLIXML'd/secret-stripped `register_task` failure to calm prose (PowerShell/module-missing, Access-denied + the D5 elevation markers from `windows`: declined/timeout/no-result/different-account/launch-failed); `else` surfaces `msg`. Non-leaking (I2). IA-4a.
- `src/ui_flet/setup_gates.py` — COUNTED pure Setup submit-gate predicates (no flet import): `can_register_schedule(config_complete, run_time)` + `can_save_sftp(host, username, remote_path, password, already_configured)`. Single-sources the schedule/SFTP gates so the disabled-button state and the Enter-to-submit (`on_submit`) handlers agree — Enter can't bypass a gate (Slice 2). Mirrors `filepicker.setup_state` (the folders gate).
- `src/ui_flet/sftp_copy.py` — COUNTED pure SFTP Test-connection trust copy (no flet): `sftp_test_copy(provenance, unsaved_edits, host, username, listing_denied=False)` (stored vs typed credential; never over-claims the nightly sync for unsaved values; appends a fixed listing-denied note last) + `sftp_form_differs_from_saved(cfg, ...)` (unsaved-edits predicate, port normalized). D6/Slice 7, 0031.
- `src/ui_flet/setup_flow.py` — COUNTED pure wizard state machine (no flet/IO — D8): five `SetupStep`s + `derive_flow(FlowInputs)->FlowState` (resume = first unsatisfied step; `can_finish` never sets `setup_completed`) + `can_advance` gate, `DeliveryFact`, byte-exact `finish_copy` (3 variants), `finish_summary_rows`/`FinishSummaryRow` (configured-vs-deferred finish checklist), `TRANSITION_CUE`, `task_args_changed`, `auto_selected_district` (D9).
- `src/ui_flet/home_status.py` — COUNTED pure Home status (no flet): `HomeStatus`/`HomeMetrics`/`FixAction` + `is_stale` + `derive_home_status` (store records + `store_created_at` fresh-start signal + injected `ScheduleStatus` (D4) — an `attention` schedule → dominant WARNING routed to Setup) + precedence (`LatestReason`/`classify_latest_reason`/`verdict_for_reason`) + SINGLE-SOURCE `ENTITY_LABELS`. IA-3a/6/8a/9.
- `src/ui_flet/schedule_status.py` — COUNTED pure tri-state schedule derivation (no flet/IO — D4): `ScheduleState` (LIVE/MISSING/UNKNOWN) + `ScheduleStatus` + `derive_schedule_status(...)` — SINGLE owner of schedule truth + copy (only `found=False`→"not scheduled"; `found=None`→UNKNOWN never asserts from hint; detects fired-but-no-record). Plus `needs_setup_badge`, `interpret_unregister`, `is_transient_location`.
- `src/ui_flet/schedule_probe.py` — Boundary seam (no flet): `probe_schedule(...)` bridges the scheduler read-back (`windows.read_schedule` — subprocess) to the pure `derive_schedule_status` + logs the config-vs-reality contradiction (durable Event-141 WARNING, PII-free). Each surface calls it OFF the UI thread; the nav model stays subprocess-free.
- `src/ui_flet/geometry.py` — COUNTED pure window-geometry decisions (0032 T2 #8, no flet): `restore_plan` (saved bounds never trusted raw — clamped into the work area, oversize shrunk, first-run height min(860, work area)) + `persist_plan` (TOTAL over garbage; maximized keeps normal bounds) + `probe_work_area` (Windows-only, else None) + single-source launch defaults; applied by the shell at boot/close.
- `src/ui_flet/about.py` — COUNTED pure About/support derivations for Help (0032 T1 #9, no flet): `RELEASE_NOTES_URL`, `version_display` (honest dev-build fallback), PII-free prefilled `support_subject`/`support_mailto` (version + district display name only — no paths, no body); rendered by the Help screen.
- `src/ui_flet/humanize.py` — COUNTED pure trust-copy helpers (no flet), all TOTAL: `friendly_district_name` (SIS id → `district_name`, unknown→raw id); `friendly_timestamp` (ISO → plain phrase); `pluralize`; SINGLE-SOURCE `friendly_anomaly_detail(count, *, variant)` (`AnomalyVariant` HOME/HISTORY/CONVERT — byte-for-byte per surface); `friendly_sftp_reason` (bounded category SFTP-failure reason, NEVER the raw core string). DS-2/IA-3/IA-9.
- `src/ui_flet/job_runner.py` — COUNTED worker-thread frame: `JobState` + `JobStateMachine` (single-flight; `start()` no-op-from-RUNNING is THE double-click guard) + `route(work, *, on_success, on_failure)` — the seam encoding the `SystemExit`-vs-`Exception` asymmetry (`SystemExit` caught FIRST → `on_failure`, never re-raised). `JobRunner.run` is `# pragma: no cover` `page.run_thread`→`page.run_task` glue (worker mutates NO control). IA-5a.
- `src/ui_flet/convert_result.py` — COUNTED pure PII-free Convert-result model (no flet): `ConvertStatus` (DELIVERED/BUILT_NOT_DELIVERED[exit-3]/BUILT_WITH_DATA_ERRORS/NEEDS_ANOMALY_ACK/NO_INPUT/NO_OUTPUT) + frozen `ConvertResult` (counts/booleans/quality — NO DataFrames) + total `summarize -> (Verdict, headline, detail)`; NEVER a raw path/`sis_type`/column. Consumes shared `humanize.pluralize`/`friendly_anomaly_detail` (CONVERT). IA-5a/IA-9.
- `src/ui_flet/convert_output.py` — COUNTED path-bearing Convert logic (no flet — D9/D10, Slice 9): `can_run_convert` run-gate (no silent fallback), `output_dir_is_set`, `resolved_output_caption` (set→names folder; unset→routed "Set your output folder in Settings"), mockable per-OS `open_folder` (`os.startfile`/`open`/`xdg-open`, never raises). The output PATH lives here — counterpart to the path-free `convert_result`.
- `src/ui_flet/run_history.py` — COUNTED pure PII-free Run-History derivation (no flet; 2nd `store.read_run_records` consumer): `HistoryBanner`/`SftpDelivery`/`RunRow` (NO `error`) + `derive_history_banner` (None→unavailable, []→fresh-start via `has_completed_setup()`+LIVE `ScheduleStatus` next-run, else classify via `home_status`, stale→WARNING) + `to_run_row(s)`. NEVER raw `error`/`ANOMALY:`. IA-6/9.
- `src/ui_flet/mapping_catalog.py` — COUNTED pure config-catalog (no flet): frozen PII-free `ConfigSummary` (name/output_labels/source_file_count/loaded_ok) + `summarize_config(sis_type, *, config_dir=None)` (TOTAL — output-CSV labels via the empty-`enabled_entities`-means-all rule through `ENTITY_LABELS`; de-duped file count; load failure→degraded, never the raw error) + `list_configs`. Reuses `available_configs`/`friendly_district_name`. IA-8a.
- `src/ui_flet/components.py` — View glue (coverage-omitted): SINGLE source of Direction B control factories — `page_header`, `section_label`, `HealthVerdictBanner` (toned verdict band), `metric_tile`, 3-tier buttons (`primary_button` filled · `secondary_button` OUTLINED · `text_button`), `card`/`hero_gradient`, `district_chip`, `status_pill`, `run_table`, `FileChip`, `ErrorCard`, `build_design_demo`. See `docs/DESIGN_SYSTEM.md`.
- `src/ui_flet/nav_rail.py` — View glue (coverage-omitted): `build_nav(..., attention_ids) -> (view, rail)` — Direction B NAVY `ft.NavigationRail` (`color_rail_bg`; on-navy labels/icons, 12% white active pill; sync-glyph brand block + `v{app_version()}`/reassurance/Exit foot). Returns the rail handle for highlight sync (D7) + `badge` mutation; `attention_badge()` = the D4 Setup dot; `dest.id` selection, index via `nav.selected_index_for`.
- `src/ui_flet/shell.py` — View glue (coverage-omitted): `main(page)` builds the themed window, `dict[id->factory]`, rail (`nav_rail.build_nav`), content area on `color_content_wash` with a ~960px left-anchored cap; `select_by_id` syncs `rail.selected_index` (D7); OFF-thread `schedule_probe`+`needs_setup_badge` raises the Setup badge (D4, Windows-only); zero-orphan close via async `_close_window`.
- `src/ui_flet/launcher.py` — View glue (coverage-omitted): `main()` does frozen-cwd (`resolve_frozen_cwd`), then `boot_logging()` (configures the shared UI file-log sink — deferred from import), then `ft.run(shell.main)` wrapped in an early-failure path — full traceback to the ETL log sink, a plain-language dialog/tkinter/stderr fallback, non-zero exit; pure helpers `boot_logging`/`resolve_log_path`/`format_user_error` are tested.
- `src/ui_flet/filepicker.py` — COUNTED boundary logic: `ft.FilePicker` async-service wrapper (`pick_directory`/`pick_files`, registered once via the tested idempotent `_ensure_picker`; only `await`-dialog glue is `# pragma: no cover`) + pure validation mirroring `run_pipeline` (`validate_input_dir` exists+is_dir, `validate_output_dir` parent-structural, effectful `check_writable` w/ TOCTOU note) + the pure `setup_state` save-gate.
- `src/ui_flet/picker_field.py` — View glue (coverage-omitted): `PickerField` (themed `ft.Column`) — label + "Browse…" button (`components.primary_button`) calling the async `pick_directory` wrapper + chosen-path display + inline valid/invalid line; takes a `validator` + `on_change(path, result)`; the one reusable picker every later surface reuses (no tkinter port).
- `src/ui_flet/screens/__init__.py` — Flet UI surfaces package marker (one module per real navigation surface; trust-critical logic stays in COUNTED pure helpers).
- `src/ui_flet/screens/setup.py` — View glue (coverage-omitted): `build_setup(page)` = first-run WIZARD until setup completed, else flat SETTINGS scroll (D8/Slice 8). Wizard: five `setup_flow` steps (skippable/reconciled Schedule+Delivery; finish-only `setup_completed`; `finish_copy`). Settings: one reconciling Save (`task_args_changed` re-registers). Both reuse `_build_schedule_section`/`_build_sftp_section`. D9: no district pre-select.
- `src/ui_flet/screens/onboarding.py` — View glue (coverage-omitted): `build_onboarding(page, *, sis_type="", on_start_setup)` — the single front door into the Setup wizard (D10/Slice 8, UNCONFIGURED Home / IA branch (a)): branded hero + `HealthVerdictBanner(Verdict.WARNING)` + ONE calm line + "Start setup" CTA (the step-by-step preview removed — it duplicated the wizard). Callback-driven; static. IA-3 reuses it.
- `src/ui_flet/screens/home.py` — View glue (coverage-omitted): `build_home(page, *, app_config, on_navigate)` three-way Home. (a) `nav.needs_setup`→`build_onboarding`; (b)/(c) `store.read_run_records` (+`store_meta`) + `derive_home_status` verdict-first; the schedule read-back (D4) is fetched OFF-thread (`schedule_probe`) and re-derives in place (MISSING/contradicted → WARNING to Setup). Never-crash `ErrorCard`. IA-3b.
- `src/ui_flet/screens/convert.py` — View glue (coverage-omitted): `build_convert(page)` manual convert (`PickerField`+`FileChip`s → `JobRunner.run` → verdict/tiles/quality; SFTP → exit-3 `BUILT_NOT_DELIVERED`) + `convert_job(...)` adapter (ack-gated `save_all` → `_record_manual_run`; run-gate + FAILS LOUD on unset output). 0031: deliver needs a stored credential (`_sftp_credential_present`) else `_delivery_not_ready_card` → Setup. IA-5a/b.
- `src/ui_flet/screens/run_history.py` — View glue (coverage-omitted): `build_run_history(page, *, app_config)` read-only. `read_run_records`+`store_meta`; verdict-first `derive_history_banner`→`HealthVerdictBanner`, then `run_table(to_run_rows[:50])` scrollable; the empty-state next-run refines from an OFF-thread schedule probe (D4). None/[]→banner; never-crash `ErrorCard`. IA-6.
- `src/ui_flet/screens/help.py` — View glue (coverage-omitted): `build_help(page, *, app_config)` — link-out Help (no bundled-docs render, per 0013 scope-lock). Hero + "Get help" card (`launch_url(HELP_CENTRE_URL)` + `mailto:SUPPORT_EMAIL`, both also offline-readable `ft.Text(selectable=True)`) + decouple-the-sync reassurance. `HELP_CENTRE_URL`/`SUPPORT_EMAIL` drift-guarded constants; never-crash `ErrorCard`. IA-7.
- `src/ui_flet/screens/mapping.py` — View glue (coverage-omitted): `build_mapping(page, *, app_config)` — select-a-pre-built-config review-and-switch surface (NOT the full editor; IA-8b deferred). Current-mapping card + a switch `ft.Dropdown` (`available_configs()` allowlist) showing the pending `mapping_catalog` summary + a gated Apply (`loaded_ok` AND ≠ current) → writes `AppConfig.sis_type` → `HealthVerdictBanner`. IA-8a.

---

## config/mappings/

- `config/mappings/myedbc_mapping.yaml` — Base config (v1.9): defines all 7 entity templates (Students, Staff, Family, Classes, Enrollments, CourseInfo, StudentCourses); `enabled_entities` defaults to the 5 standard rostering CSVs; sets homeroom grades, school-year source, and course-code exclusion patterns.
- `config/mappings/sd40myedbc_mapping.yaml` — SD40 New Westminster override (`_base: myedbc`): CSV source file names, headerless schedule with injected column headers, `{student number}@newwestschools.ca` email, `excluded_course_codes` for ATT--AM/PM/Daily attendance rows.
- `config/mappings/sd48myedbc_mapping.yaml` — SD48 Sea to Sky override (`_base: myedbc`): remaps to `StudentDemographicEnhanced.txt` and `StaffInformation.txt`; no other deviations from base.
- `config/mappings/sd51myedbc_mapping.yaml` — SD51 Boundary override (`_base: myedbc`): `StudentDemographicEnhanced.txt`, `{student number}@sd51.bc.ca` email, fixed hardcoded academic start/end dates (bypasses auto-detection).
- `config/mappings/sd54myedbc_mapping.yaml` — SD54 Bulkley Valley override (`_base: myedbc`): lowercase source file names (studentschedule, courseinformation, staffinformation, classinformationenhanced .txt), non-Enhanced staffinformation for Staff, EmergencyContactInformationEnhanced for Family, `{legal surname}.{usual first name}@sd54.bc.ca` email, ATT--AM/PM/Daily excluded; academic dates auto-derive from School Year.
- `config/mappings/sd60myedbc_mapping.yaml` — SD60 Peace River North (`_base: myedbc`): enhanced sources (`Student_demo_enh.txt`/`EmergencyEnhanced.txt`); section→`Section`, title→`Title`; `ATT--AM/PM` excluded; Family guardians-only `row_filters`; `cross_enrollment.collapse` home-school dedupe; emails GENERATED `firstlast+admission-yy@learn60.ca` (`sanitize`+`derived_dates`); `Active No Primary` DROPPED; `SchoolCode → "Home school number"`.
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

- `tests/conftest.py` — Shared fixtures (synthetic DataFrames, YAML configs, `DataTransformer` instances) + the autouse `isolated_user_profile` fixture (redirects the `paths.user_data_dir` seam to a tmp dir, swaps an in-memory keyring backend, restores/close logging handlers) and the `real_profile_baseline` snapshot for the canary; `real_user_data_dir` marker opts a test out of the seam patch.
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
- `tests/test_app_config.py` — `AppConfig` load/save round-trip, unknown-field tolerance, default values; `has_completed_setup()` + `setup_completed` back-compat inference on load (D4a — no deployed install regresses into onboarding).
- `tests/test_isolation_canary.py` — D3 isolation tripwire: `AppConfig.save()` + `get_logger()` under the autouse isolation fixture leave the real user-data profile (config.json/etl_tool.log/history.db in `paths.user_data_dir()`) byte-untouched vs the conftest-import baseline, and the writes land in the isolated tmp dir.
- `tests/test_entry_logging.py` — Entry-path logging (D3): CLI (`_configure_cli_logging`) and Flet launcher (`boot_logging`) each attach a file handler resolved through the paths seam; a fresh-interpreter import of `src.main` configures NO file sink (import-time-pollution regression guard).
- `tests/test_main_helpers.py` — Pipeline helper functions: `_check_anomalies`, `_emit_run_log`, `extract_required_files`, `_sftp_upload`, `_print_diff`.
- `tests/test_history_store.py` — `src/history/store.py` contract: round-trip + newest-first ordering; reader `[]`-vs-`None` split (missing/empty→`[]` no-create, corrupt/locked→`None`, malformed payload skipped); non-fatal writes; quarantine-and-recreate; source coercion; higher-`user_version` never-downgrade; `created_at` meta; connection hygiene (removable DB file).
- `tests/test_pipeline_run_store.py` — Run-store wiring in `run_pipeline` + `convert_job`: `source` propagation (env→scheduled / default cli / explicit wins / bogus→unknown), enriched log-line ↔ stored-record parity (privacy split), strictly-non-fatal writes (result/CSVs unchanged; failure path preserves the original ETL exception identity), record-shape equivalence through `derive_home_status`/`to_run_rows`, and `convert_job`→manual.
- `tests/test_cli.py` — CLI flags: `--dry-run`, `--diff`, `--quality`, `--version` (calls `run_pipeline()` directly, bypasses argparse).
- `tests/test_sftp_uploader.py` — `SFTPUploader` with mocked paramiko and keyring: store/retrieve password, `test_connection()`, `upload_csvs()` zip-and-put flow.
- `tests/test_sftp_cli.py` — SFTP CLI subcommands: `--sftp-configure` (env var + stdin password sources), `--sftp-test`, `--sftp-show`, host allowlist rejection, flag mutual-exclusion.
- `tests/test_sftp_integration.py` — Live SFTP integration using `pytest-sftpserver` (real paramiko transport); skipped automatically if the package is absent.
- `tests/test_schedulers.py` — Windows Task Scheduler and Linux cron wrappers with all subprocess calls mocked; `TestReadSchedule` covers the D4 tri-state read-back parse fixtures (found/definitively-absent/denied/timeout/PowerShell-missing/non-Windows/never-run) + the injection-free fixed script.
- `tests/test_scheduler_runas.py` — Unit tests for `register_task` run-as behavior (pinned to the already-elevated DIRECT path via an autouse `is_elevated -> True`): asserts the explicit `-LogonType Password`/`Highest`/`Limited` principal when a password is supplied, omitted when not (back-compat), password never in argv/script/env/message (and, by construction, never logged), and `validate_run_as_user` accepts/rejects correctly.
- `tests/test_scheduler_elevation.py` — Per-operation elevation (D5): real DPAPI round-trip (Windows-only; entropy/tamper fail closed), request/result handshake, ShellExecuteEx outcome mapping via mocked ctypes seams (System32-pin), orphan sweep, self-elevated `register_task`/`delete_task_elevated` flows (read-back-confirmed, **password never in argv/env/encoded-command**), fixed bootstrap script text.
- `tests/test_sftp_exit.py` — CLI exit-code tests for SFTP failure path: asserts exit 3 when SFTP is attempted and fails (with output CSVs still present on disk), exit 0 on success or when `--sftp` is absent, and exit 0 on `--dry-run --sftp` (no upload attempted).
- `tests/test_registry.py` — Transformer registry: known entity lookup, `DefaultTransformer` fallback for unregistered entities.
- `tests/test_source_config.py` — Source-config normalisation (`normalize_source_config`) and `get_source_file()` retrieval from context.
- `tests/test_helpers.py` — `src/utils/helpers.py` utilities: `normalize_columns()`, `ensure_directory()`, `district_slug()`, `build_zip_name()`, etc.
- `tests/test_paths.py` — `src/utils/paths.py` helpers (source-install + frozen `sys.frozen`): `app_icon_path()`, `user_history_db()` call-time seam, per-OS `platformdirs` resolution (Win/macOS/Linux + XDG; verbatim `DistrictSync` leaf), the new-vs-legacy rule, and `migrate_legacy_data_dir()` (fresh no-op, full move + WAL sidecars + breadcrumb, idempotence, failure-injection = legacy stays live, no data loss); marked `real_user_data_dir`.
- `tests/test_ui_flet_setup_gates.py` — `src/ui_flet/setup_gates.py` pure submit-gate predicates (`can_register_schedule` / `can_save_sftp`) truth tables — the Enter-can't-bypass-the-button guarantee (Slice 2).
- `tests/test_ui_flet_convert_output.py` — `src/ui_flet/convert_output.py` (D9/D10, Slice 9): `can_run_convert` gating table (no district / empty output / invalid input), `output_dir_is_set` + `resolved_output_caption` derivation, and mocked per-OS `open_folder` dispatch (Windows/macOS/Linux + blank-path + failure).
- `tests/test_ui_flet_sftp_copy.py` — `src/ui_flet/sftp_copy.py`: the SFTP Test success-copy truth table (stored/typed × saved/unsaved — never promises the nightly sync for unsaved values) + the `sftp_form_differs_from_saved` unsaved-edits predicate (D6/Slice 7).
- `tests/test_ui_flet_setup_sftp.py` — view-level SFTP wiring (drives the real `_test`/`_save` handlers, in Settings mode): the red-first clobber pin (a failed Test leaves the stored credential intact), typed password threads to `test_connection(password_override=...)` not `store_password`, provenance/unsaved wiring, keyring written exactly once on Save (D6/Slice 7).
- `tests/test_ui_flet_setup_flow.py` — `src/ui_flet/setup_flow.py` full transition table (D8/Slice 8): step scaffolding, resume-from-each-state derivation, schedule/delivery satisfaction honesty (UNKNOWN/MISSING/tested_failed never satisfy), per-step Enter-advance gate, the no-step-flips-`setup_completed` invariant, the three byte-exact `finish_copy` variants, the `task_args_changed` truth table, and district auto-select-iff-one.
- `tests/test_ui_flet_schedule_status.py` — `src/ui_flet/schedule_status.py` full precedence table (D4): LIVE/MISSING/UNKNOWN × hint × the fired-but-no-record contradiction, the UNKNOWN-never-asserts-scheduled honesty invariant, the badge model, `is_transient_location`, and idempotent `interpret_unregister`.
- `tests/test_ui_flet_schedule_probe.py` — `src/ui_flet/schedule_probe.py` boundary (read-back→derive→log) with `read_schedule` mocked: tri-state mapping + the config-vs-reality contradiction WARNING (Event-141 trace, PII-free), no-warn on clean/unexpected-missing.
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
