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
