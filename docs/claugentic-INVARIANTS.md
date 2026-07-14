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

- **Every app-data path (config.json, etl_tool.log, history.db) resolves at CALL time through the single `paths.user_data_dir()` seam — never a module-level constant; resolution order is new-if-exists → legacy-if-exists → else create-new.** _(Plan 0029 Slices 4a/4b/11, 2026-07-08 · `src/utils/paths.py` + `src/config/app_config.py` + `src/history/store.py`.)_ A module-level `Path.home()/".districtsync"` constant (AppConfig's old bypass) or a shallow-name patch is un-isolatable: the autouse test fixture patches the deep seam, so any consumer binding a path at import time writes the REAL profile in tests (the canary + the SD74/contract module-scoped-fixture leak both proved this). The deterministic order is load-bearing for the Slice-11 migration: `migrate_legacy_data_dir()` runs before `get_logger()` without a read prematurely materializing the new dir. Do not hoist these paths to module constants; do not create the new dir on a read.

- **Only a definitively-absent schedule read-back (`found=False`) may claim "not scheduled"; a query failure (`found=None`) is UNKNOWN and NEVER falls back to asserting "scheduled" from the config `schedule_registered` flag.** _(Plan 0029 Slice 5, 2026-07-08 · `src/ui_flet/schedule_status.py` + `src/scheduler/windows.py`.)_ The Event-141 honesty fix: a deleted task must not masquerade as scheduled off a stale boolean, and an elevated-registered task unreadable by a filtered token must not be reported as missing. A displayed next-run comes ONLY from the OS `NextRunTime`, never the config `schedule_time` (hint-as-truth — closed structurally by removing the `hint_time` param). The fired-but-no-record contradiction triggers on a record GAP only (a real `last_run` newer than the newest record), never a benign non-zero `LastTaskResult` (exit-3 writes a legitimate record). Do not reintroduce a config-boolean fallback on UNKNOWN.

- **The elevation password crosses the UAC boundary ONLY inside a DPAPI CurrentUser-scoped sealed file — never argv, never env, never a log — and registration success is CONFIRMED by read-back, never assumed from a child exit code.** _(Plan 0029 Slice 6, 2026-07-08 · `src/scheduler/elevation.py` + `src/scheduler/windows.py`.)_ CurrentUser scope IS the confidentiality boundary: consent under a different admin SID cannot decrypt → the child fails closed (`DSYNC_DIFFERENT_ACCOUNT`). NEVER widen to LocalMachine (any box account could decrypt — downgrades a domain credential). The elevated child runs the ABSOLUTE System32 powershell.exe (PATH-hijack), under a bounded wait (never INFINITE), and its message passes `_clean_ps_stderr` + the `DSYNC_`-strip before surfacing. `read_schedule` confirms register (`found=True`) and delete (`found=False`); a timeout/no-result resolves via the same read-back or hedges honestly. Do not pass the password on argv/env; do not widen DPAPI scope; do not trust the exit code.
