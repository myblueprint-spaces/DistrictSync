# Invariants (claugentic harness)

Load-bearing constraints that **must stay true or something breaks**. Each entry
is a non-obvious "must hold" rule that already bit (or would bite) if a future
change "simplified" it. Consult this before changing the named subsystem.

---

- **Unattended Windows scheduling requires a stored-password logon (`LogonType=Password`), NEVER `S4U`.** _(Plan 0009, 2026-06-25 · `src/scheduler/windows.py`.)_
  The daily scheduled run that uploads via SFTP must run **whether or not the
  setup user is logged on** AND must have a **network token** (to reach the
  SpacesEDU SFTP host). Only a stored-credential logon
  (`New-ScheduledTaskPrincipal -LogonType Password` + `Register-ScheduledTask
  -User -Password`) provides both. `S4U` runs logged-off **without** storing a
  password, but it has **no network token** — the task would run yet silently
  fail to deliver. `S4U` (and the loose `-User/-Password/-RunLevel`
  parameter-set inference that can degrade to it) is therefore **rejected by
  design**; the explicit `-LogonType Password` principal is the **documented way
  to force** `TASK_LOGON_PASSWORD` (rather than rely on parameter-set
  inference). **Proof-it-took (pending user verification):** the registered task
  must query as `LogonType = Password` / `RunLevel = Highest`, and a logged-off
  run must reach SFTP. Do not "simplify" the principal to S4U or rely on
  parameter-set inference.

---

- **The run-history store schema is ADDITIVE-ONLY; the WRITE path is its sole creator/migrator; a higher `user_version` is NEVER migrated or downgraded; and a store write is STRICTLY NON-FATAL and never masks the original ETL exception.** _(Plan 0029 Slice 4b, 2026-07-08 · `src/history/store.py` + `src/etl/pipeline.py`.)_
  Two exe versions share one `history.db` on a district server: the pinned scheduled
  exe and an updated UI. That forces four load-bearing rules — break any one and you
  either brick the ledger, corrupt cross-version reads, or (worst) turn a best-effort
  history write into a failed nightly sync:
  - **Additive-only schema (no migration framework — YAGNI).** New schema versions may
    only ADD nullable/defaulted columns, never rename/drop/retype. Every statement names
    columns explicitly (no `SELECT *`, no positional `INSERT`) so a v1 writer stays valid
    against a v2 DB and a v2 reader stays valid against a v1 row. Bump `SCHEMA_VERSION`
    only for an additive change; do NOT add a migration engine.
  - **The write path is the sole creator/migrator.** `write_run_record` creates the
    schema, stamps `PRAGMA user_version` on a brand-new DB, sets WAL + `busy_timeout`
    (DELETE-journal fallback), and hardens Unix perms. `read_run_records` / `store_meta`
    must NEVER create the DB — a missing DB reads as `[]` / `None`, so a read on a fresh
    install can't materialize an empty store and mask "no runs yet".
  - **A higher `user_version` is never migrated or downgraded.** A writer that sees a
    `user_version` above the one it knows writes with named columns only and leaves the
    version untouched — an old pinned exe must not "helpfully" rewrite a newer UI's schema.
  - **The store write is strictly non-fatal and never masks the ETL exception.** Any
    `sqlite3.Error`/`OSError` logs a WARNING and returns `False` (the enriched
    `__DISTRICTSYNC_RUN__` log line is the durable fallback). At the pipeline FAILURE site
    the record/log/store block is guarded so it can never raise — the bare `raise`
    re-raises the ORIGINAL ETL exception (identity preserved). A corrupt "malformed image"
    is quarantined (`history.corrupt-<ts>.db`) and recreated, so one torn write can't brick
    the ledger forever. **Proof-it-holds:** `test_pipeline_run_store.py` asserts a forced
    store failure changes neither the `PipelineResult`/exit code nor the CSVs, and that the
    failure path re-raises the original `RuntimeError` (not the store error).
  - **A dry run never enters the ledger, and `dry_run` is REQUIRED keyword-only at every pipeline store sink.** _(Amended 2026-07-29, plan 0038 S1.)_ `--dry-run` writes no files, so a stored `success` record paints a sync on Home and a phantom Run History row. The gate lives at `_store_run_record` — the pipeline's single sink — and `dry_run` carries **no default** at `_store_run_record`/`_record_early_failure`, so a newly-added `sys.exit` path cannot silently record a preview. The `__DISTRICTSYNC_RUN__` log line still fires (the log/store split is deliberate). `screens/convert.py`'s direct `write_run_record` call sits OUTSIDE this gate by design — give it its own if Convert ever gains a preview. **Proof-it-holds:** `tests/test_pipeline_run_store.py` (no record / `history.db` untouched, incl. failure + early-exit dry runs) + exe smoke phases 2↔3, which pin absent-then-created.

- **Every app-data path (config.json, etl_tool.log, history.db) resolves at CALL time through the single `paths.user_data_dir()` seam — never a module-level constant; resolution order is **`DISTRICTSYNC_DATA_DIR` (step 0, wins outright) → new-if-exists → legacy-if-exists → else create-new**.** _(Plan 0029 Slices 4a/4b/11, 2026-07-08 · `src/utils/paths.py` + `src/config/app_config.py` + `src/history/store.py`.)_ A module-level `Path.home()/".districtsync"` constant (AppConfig's old bypass) or a shallow-name patch is un-isolatable: the autouse test fixture patches the deep seam, so any consumer binding a path at import time writes the REAL profile in tests (the canary + the SD74/contract module-scoped-fixture leak both proved this). The deterministic order is load-bearing for the Slice-11 migration: `migrate_legacy_data_dir()` runs before `get_logger()` without a read prematurely materializing the new dir. Do not hoist these paths to module constants; do not create the new dir on a read. **Amended 2026-07-29 (plan 0038 S1 · `src/utils/paths.py` + `scripts/ci_flet_pack_smoke.py`):** step 0 is the `DISTRICTSYNC_DATA_DIR` support/test override. It exists because `platformdirs` resolves the Windows dir via `SHGetKnownFolderPath` and **ignores `LOCALAPPDATA`**, so a FROZEN exe cannot otherwise be pointed at a throwaway profile. It **fails LOUD and never falls back**: a relative value raises `ValueError` (a frozen exe's CWD is a `_MEIPASS` deleted on exit; a scheduled task's is `System32`), an unusable dir raises `RuntimeError` (a silent fallback writes the profile where the operator did not ask and does not know to look). `migrate_legacy_data_dir()` is suppressed **only** while the override points ELSEWHERE — one aimed AT the platform dir would strand `~/.districtsync`. Consequence for tooling: anything writing the profile from outside the test fixture (`ci_flet_pack_smoke.py --cli-smoke`) **REFUSES to run without the override** rather than warning — an unset seam would both corrupt the developer's real profile and make the check vacuous. Do not add a silent fallback; do not absolutize a relative value; do not widen the migration no-op.

- **The admin's identity email lives in the settings DIRECTORY and on two screens — never in a log, a run record, the store, an output CSV, or the CLI — it is RE-VALIDATED at read time, and the identity layer can never fail closed.** _(Plan 0038 S3/S4a, 2026-07-29 · `src/config/app_config.py` · `src/ui_flet/identity_gate.py` · `src/ui_flet/screens/identity.py` · `src/ui_flet/shell.py`.)_
  The launch page is IDENTIFICATION for list-scoping, not authentication, and the address it collects is personal data in a product whose other data is student PII. Four rules hold it together — break any one and you either leak an address or lock an admin out of their own sync:
  - **Containment is the DIRECTORY, not one file — and the sweep is bounded, so the copy that describes it must be too.** `config.json` **and every `config.corrupt-*.json` sibling** (byte-for-byte predecessor copies `_preserve_unreadable_predecessor` writes and nothing else prunes). "Blank clears" routes through `AppConfig.identity_clear`, which unlinks those siblings after a confirmed write — with **two** gates, both load-bearing: never on a refused write (on an UNREADABLE profile nothing was cleared, and those copies may be the admin's only recoverable settings), and never when there was **nothing stored to erase** (the population most likely to own a quarantine copy is the one whose settings went unreadable, who may never have answered at all — purging on a no-op Save would destroy real data to erase a value that was never there). Clearing also resets `identity_prompt_dismissed`, or the states wedge: no stored identity and no surface willing to ask again. **What the sweep does NOT reach, stated rather than implied:** an unlink can fail (a locked file), so `identity_clear` returns `ClearOutcome(cleared, removed, remaining)` and the admin-facing note branches on it — "we also deleted the older copies" is a false claim in two of the three outcomes, including the one where every unlink failed; and a crash between `mkstemp` and `os.replace` can strand a `.config.json.<rand>.tmp` staging file holding a full payload, which no erasure sweeps. So: containment is "`config.json` + its quarantine siblings, swept best-effort and reported honestly", not "guaranteed gone from the folder". **Proof-it-holds:** `tests/test_identity_pii_guards.py::TestTheErasurePathCoversTheCopies` (incl. the nothing-stored, refused-clear and locked-copy-note rows) + `tests/test_ui_flet_identity_page.py::TestTheSettingsSection`.
  - **Every write goes through `identity_save` / `identity_clear`.** They re-check `settings_unreadable()` on the instance about to be written, validate KEY *and* VALUE before applying any of them, and refuse a non-`identity_*` key loudly — so identity resolution structurally cannot rewrite `sis_type`. Changing WHO looks after the sync never changes WHICH district converts.
  - **The stored value is re-validated at READ time.** `config.json` is hand-editable and the load-time check validates the TYPE, not the shape, so `identity_gate.stored_identity_email` runs `validators.validate_identity_email` before ANY surface renders it; a failure reads as UNANSWERED (re-ask), never as "echo it anyway". Do not render `cfg.identity_email` directly. **Proof-it-holds:** the hostile-value table in `tests/test_ui_flet_identity_resolve.py` + the Settings/Help non-echo rows.
  - **Logging is counts-only, and the layer never fails closed.** `identity gate: shown=… reason=…` (a bounded vocabulary) and `identity resolve: outcome=… matched_districts=… configs_with_domains=…` — the address, its local part AND its domain are all banned. Any exception in the predicate, the page build, or resolution logs and calls `_enter_app()`; the close handlers are bound ABOVE the gate (the launch page has no rail and no Exit, so the title-bar close is its only exit). **Proof-it-holds:** `tests/test_ui_flet_shell_boot.py::TestTheIdentityFloor` (with its non-vacuous positive twin) + `tests/test_identity_pii_guards.py::test_the_launch_gate_and_resolution_log_counts_only`. Matching stays EXACT domain equality — a suffix match would OVER-match, which is the dangerous direction under fail-open.

- **Only a definitively-absent schedule read-back (`found=False`) may claim "not scheduled"; a query failure (`found=None`) is UNKNOWN and NEVER falls back to asserting "scheduled" from the config `schedule_registered` flag.** _(Plan 0029 Slice 5, 2026-07-08 · `src/ui_flet/schedule_status.py` + `src/scheduler/windows.py`.)_ The Event-141 honesty fix: a deleted task must not masquerade as scheduled off a stale boolean, and an elevated-registered task unreadable by a filtered token must not be reported as missing. A displayed next-run comes ONLY from the OS `NextRunTime`, never the config `schedule_time` (hint-as-truth — closed structurally by removing the `hint_time` param). The fired-but-no-record contradiction triggers on a record GAP only (a real `last_run` newer than the newest record), never a benign non-zero `LastTaskResult` (exit-3 writes a legitimate record). Do not reintroduce a config-boolean fallback on UNKNOWN.

- **The elevation password crosses the UAC boundary ONLY inside a DPAPI CurrentUser-scoped sealed file — never argv, never env, never a log — and registration success is CONFIRMED by read-back, never assumed from a child exit code.** _(Plan 0029 Slice 6, 2026-07-08 · `src/scheduler/elevation.py` + `src/scheduler/windows.py`.)_ CurrentUser scope IS the confidentiality boundary: consent under a different admin SID cannot decrypt → the child fails closed (`DSYNC_DIFFERENT_ACCOUNT`). NEVER widen to LocalMachine (any box account could decrypt — downgrades a domain credential). The elevated child runs the ABSOLUTE System32 powershell.exe (PATH-hijack), under a bounded wait (never INFINITE), and its message passes `_clean_ps_stderr` + the `DSYNC_`-strip before surfacing. `read_schedule` confirms register (`found=True`) and delete (`found=False`); a timeout/no-result resolves via the same read-back or hedges honestly. Do not pass the password on argv/env; do not widen DPAPI scope; do not trust the exit code.

---

- **A rostering gate keys on the RESOLVED scope, never on the PRESENCE of the config key that used to be its only source.** _(Plan 0042 slice 1b, 2026-08-13 · `src/etl/transformers/blended.py` + `src/etl/transformers/grades.py`.)_
  `grades.resolve_timetable_scope` returns `set[str] | None`, and TWO different
  config keys can now produce a positive set: `class_rostering_grades` directly,
  or `student_rostering_grades` via the inherited bound (`student − homeroom`,
  applied when the class key is ABSENT). Every consumer must therefore branch on
  `timetable_scope is not None` — never on `global_config.get("class_rostering_grades")`
  and never on truthiness (an EMPTY set is a real scope: "roster no timetable
  classes at all").
  **Amended 2026-08-14 (plan 0043):** the `None` branch no longer means "nothing
  is suppressed" — it means "no scope was CONFIGURED", and the effective rostered
  set is then DERIVED (`CEDS − homeroom`). Both readings now live in exactly one
  place, `grades.timetable_rostered_grades`; the blend gate is unconditional and
  consumes that. So the rule binds harder, not less: do not re-derive the
  complement at a call site, and do not reintroduce an `is not None` guard around
  the gate itself. The resolver's `None` survives for ONE remaining reader — the
  suppression log, which must not print a 24-code "configured scope" a district
  never configured.
  **What breaks otherwise, concretely.** A gate keyed to the class key's presence
  is silently dead on exactly the path the inherited bound was introduced to make
  safe: blended detection is deliberately unscoped, `_emit_missing_blended_classes`
  emits any blend the subject path missed, and `_blended_teacher_enrollments`
  emits teacher rows with no roster filter — so a blend in an unlicensed grade
  survives as a `BLENDED_` class with a teacher and **zero students**, for grades
  the district is not sending. It raises **no** quality warning (the class IS in
  `Classes.csv`, so the orphan check sees nothing) and no anomaly beyond the
  expected first-run drop, and it is byte-shaped like the partner-ingest rejection
  commit `e187ac8` fixed. The suppression must also stay BEFORE the first
  `result.*` write in `_register_blends` (`class_map`/`teacher_map` are populated
  before the grade range is known), or the suppression itself creates the orphans.
  **Proof-it-holds:** `tests/test_student_rostering_grades.py::TestSD74StudentScopeDifferential`
  pins both sides — an out-of-scope blend absent from `Classes.csv` AND
  `Enrollments.csv` together, and a surviving blend that still carries students —
  on a run where the class key is absent. Re-keying the gate to the key's presence
  turns both red.
  **The general rule, worth carrying to the next scope key:** a feature flag's
  presence check and its resolved value stop being interchangeable the moment a
  second input can produce the value.

---

- **The blend-suppression gate must derive its grades from the SAME ROWS, with the SAME null handling, as `split_by_homeroom_grades(keep="subject")` — "row-set identity".** _(Plan 0043 slice 2, 2026-08-14 · `src/etl/transformers/blended.py` + `src/etl/transformers/grades.py`.)_
  Since 0043 a blend is suppressed when NONE of its enrollable grades is in
  `grades.timetable_rostered_grades(...)`. Sharing that grade VOCABULARY is
  necessary but **not** sufficient: the gate and the subject mask must also be
  looking at the same rows. `BlendedClassDetector._build_enrollable_grade_map`
  therefore takes every schedule row of each section — **no `dropna()`, no
  `if grade:`** — and converts through `grades.ceds_grade_series`, the very
  function the subject split uses.
  **What breaks otherwise, concretely.** The natural implementation builds the
  map beside `_build_grade_map`, inheriting its `.dropna()`. But a blank/NaN
  grade converts to `"UG"`, `"UG"` is not a homeroom grade, so that row SURVIVES
  the subject filter and is a real student. A `dropna`-built map cannot see it:
  a blend of `MT1` (rows `"03"`, `"03"`, NaN) + `MT2` (`"04"`) under the default
  KG–07 homerooms yields `enrollable = {"03","04"}` ⇒ suppressed ⇒ that pupil
  falls back to `MT1_<year>` via `assign_class_ids`. `Classes.csv` **GROWS**, a
  live Class ID is **RE-ASSIGNED**, and a blend that HAD a student was dropped —
  the exact opposite of the "strictly subtractive" property the per-row design
  was chosen for, and invisible from either call site. The same applies to any
  future consumer that asks "which pupils would be rostered here?".
  **Proof-it-holds:** `tests/test_class_rostering_grades.py::TestRowSetIdentityUnderBlankGrades`
  (the blank-grade blend survives, no per-section class appears, the pupil's only
  enrollment is the blended one — paired with the differential twin that the same
  blend minus that one row IS suppressed) +
  `tests/test_blended_classes.py::TestEnrollableGradeMapIsRowSetIdentical`.
  Injecting `.dropna()` into the builder turns both red.
  **The standing PREMISE the invariant rests on — one file, two readers.** "Same
  rows" is only meaningful because the gate and the subject split read the SAME
  schedule. The gate runs inside blended detection, which is invoked with the
  **Classes** entity's `student_schedule` (`classes.py`), while the subject split
  that must agree with it runs on the **Enrollments** entity's
  (`enrollments.py`). Today those are one file **in every shipped config** —
  checked, not assumed: four districts DO override `student_schedule`
  (`sd40myedbc` → `SD-40_StudentSchedule.csv`, `sd54myedbc` → lowercase,
  `sd60myedbc` and `sd74myedbc` → `StudentCourseSelection*` — they never read
  the base's `StudentSchedule.txt` at all), and each points **both** entities at
  the same file. So the guarantee rests on that per-config agreement, NOT on the
  base default and NOT on the absence of overrides.
  **If a config ever points them at different files the invariant is broken even
  with the code unchanged** — a blend
  suppressed on the Classes-side rows while Enrollments-side rows are
  timetable-side re-keys those students to `MT#_<year>` with no matching Classes
  row, i.e. the orphan Class IDs commit `e187ac8` was written to stop. Whoever
  first splits those two source files owns re-establishing this.
  **The general rule:** two filters that must agree need to share their ROW SET
  and their NULL POLICY, not merely their value vocabulary — a shared constant
  is not a shared decision, and neither is a shared column name when the rows
  behind it can come from different files.
