# Invariants (claugentic harness)

Load-bearing constraints that **must stay true or something breaks**. Each entry
is a non-obvious "must hold" rule that already bit (or would bite) if a future
change "simplified" it. Consult this before changing the named subsystem.

---

- **Every principal comparison reduces through `setup_gates.principal_key`, and the run-as field SENDS the TYPED value (stripped only) — while the delivery line keeps naming the KEYRING OWNER, never the task principal.** _(Plan 0046 B, 2026-09-16 · `src/ui_flet/setup_gates.py` (`principal_key`, `register_block`) + `src/ui_flet/setup_flow.py` (`schedule_reconcile`) + `src/ui_flet/screens/setup.py` (`_account_facts`, `_keyring_owner_account`) + `src/scheduler/windows.py` (`register_task`).)_
  The run-as field is PREFILLED with the signed-in account (or, on a live task, the recorded principal), and the ONLY thing that keeps that safe is `register_task`'s own `requested.casefold() != current.casefold()` — naming your own account is not a principal change. Re-casing, domain-qualifying or otherwise sanitising the value at the view flips that comparison, and it breaks in BOTH directions: one way, twenty districts' ordinary blank-password register starts refusing with `_MSG_ACCOUNT_NEEDS_PASSWORD` (a G5 regression on every install); the other way, a genuinely different account slips past the gate. So `principal_key` is the ONE reduction the gate, the reconcile, the record and the delivery note share, and it is used ONLY to decide gates and notes — never to decide what to SEND. Sending `None` for an account the view "knows" is the current one would be exactly the silent substitution Slice 1 made unrepresentable. Second half, and the reason the A6 rename came FIRST: `screens/setup.py`'s *"Your delivery password is saved and readable by …"* names `_keyring_owner_account()` — the account DistrictSync is RUNNING as — and must never be repointed at the task principal. Credential Manager has no cross-user scope (`CRED_PERSIST_ENTERPRISE` is per-user, Documented), so a task on a service account cannot read the admin's credential; naming the principal there prints a false all-clear on the single most likely real failure this feature creates. **Proof-it-took:** the G5 register test (asserted on the REGISTERED params, not the return tuple), the `principal_key` ↔ `register_task` two-implementation parity table, and the pinned delivery string driven under a recorded FOREIGN principal with its positive twin (the named account is asserted NOT to be the registered one).

- **Unattended Windows scheduling requires a stored-password logon (`LogonType=Password`), NEVER `S4U`.** _(Plan 0009, 2026-06-25 · `src/scheduler/windows.py`, `src/scheduler/task_com.py`.)_
  The daily scheduled run that uploads via SFTP must run **whether or not the
  setup user is logged on** AND must have a **network token** (to reach the
  SpacesEDU SFTP host). Only a stored-credential logon provides both. `S4U` runs
  logged-off **without** storing a password, but it has **no network token** —
  the task would run yet silently fail to deliver. `S4U` (and the loose
  parameter-set inference that can degrade to it) is therefore **rejected by
  design**. Since the COM move (plan 0041 S1b) the mechanism is
  `task_com.apply_definition`, which passes the explicit `TASK_LOGON_PASSWORD`
  constant to `RegisterTaskDefinition` — never inference — and `TASK_LOGON_S4U`
  (2) is deliberately **not even defined** in that module, so the unsafe value is
  unrepresentable rather than merely unused. **Proof-it-took (pending user
  verification):** the registered task must query as `LogonType = Password` /
  `RunLevel = Highest`, and a logged-off run must reach SFTP. Do not "simplify"
  the principal to S4U or reintroduce the constant.

---

- **The task principal is DECLARED (`task_com.PrincipalKind`), never inferred from whether a password is present — and no guard anywhere may key on the password to mean "unattended".** _(Plan 0049 S-3, 2026-09-18 · `src/scheduler/task_com.py`, `src/scheduler/windows.py`, `src/scheduler/__init__.py`, `src/scheduler/elevated_apply.py`, `src/utils/validators.py`.)_
  `password is not None` has exactly two answers, so a third principal shape — a
  managed service account, which is UNATTENDED and carries NO password — is
  inexpressible under it; and an absent password is equally what a blank field, a
  cleared UI local and a dropped payload key look like. The kind is therefore a
  required, undefaulted field on both `Principal` (the REQUEST) and `RegisterParams`
  (the COMMAND), `apply_definition` has ONE `RegisterTaskDefinition` call site and no
  `if` at all, and the kind→logon table lives once in `logon_type_for`.
  Three consequences that are the actual invariant, because each was a live bug the
  moment a third kind existed: **(a)** the elevation predicate is
  `kind is not INTERACTIVE_TOKEN`, not `has_password` — a passwordless MSA keyed on the
  password would take the DIRECT path to a certain access-denied; **(b)**
  `CronScheduler.register` refuses on the KIND — its `run_as_password is not None`
  guard passed an MSA straight into a cron line that silently dropped the account;
  **(c)** `elevated_apply._do_register` chooses the account validator by kind through
  `task_com.validate_principal_account` — and since EVERY unattended register
  self-elevates through that function, a `validate_run_as_user` whose charset has no
  `$` made gMSA unreachable no matter what the rest of the engine could express. That
  dispatch is the ONE kind→validator spelling for all four call sites in both
  processes. `validate_gmsa_account` is a SHAPE check (its docstring says so) that
  refuses BY NAME, in both halves, `<COMPUTERNAME>$` and every built-in authority — a
  computer account has the gMSA shape and is SYSTEM-equivalent. The request/command
  asymmetry is deliberate and must not be "tidied": `Principal` ALLOWS an
  interactive-token request naming a foreign account so the boundary can answer with
  `_MSG_ACCOUNT_NEEDS_PASSWORD`, and `RegisterParams` carries that fifth refusal
  because the elevated child builds one from an unsealed request file. **Proof-it-took:**
  eleven mutations (revert each guard to its password-shaped form, drop each refusal,
  map MSA to `TASK_LOGON_SERVICE_ACCOUNT`, stop reading the principal back) each turn a
  named test RED, and every refusal row has a positive twin.

---

- **No string `task_com._canonical_message` can RETURN may carry a marker another consumer keys on (`messages.ABSENT_TASK_MARKERS`, `ACCESS_DENIED_MARKERS`, `SECRET_SENTINEL_PREFIX`) unless it is the code that owns it — and every `MSG_`/`_MSG_`-named message binding swept in `task_com`/`windows`/`elevated_apply` has exactly ONE name.** _(Plan 0047, 2026-09-16 · `src/scheduler/task_com.py`, `src/scheduler/messages.py`.)_ The injectivity claim is scoped to what the sweep actually collects — a binding whose name starts with `MSG_`/`_MSG_` in those three modules. An inline literal at a `_fail` call site, or `linux.py`'s crontab messages, is invisible to it; those stay model-upheld, not swept.
  Three different consumers key on substrings of a schedule failure message, and
  a fourth keys on the whole string by exact equality:
  `schedule_status.interpret_unregister` turns an absent-task marker into the
  success-shaped "No schedule was registered", after which
  `screens/setup.py` persists `schedule_registered = False` **over a task that
  is still live**; `src/scheduler/__init__.py`'s delete adapter fires its one
  elevated UAC retry on an access-denied marker; `windows._sanitize_child_message`
  collapses any `DSYNC_`-bearing message, so a canonical carrying that prefix
  would collapse on the elevated path only; and
  `task_com.hresult_for` recovers an HRESULT from a WHOLE canonical string by exact
  equality (consumed by `windows._fail`'s elevated arms, and by the classifier from
  Slice A2 on), which is sound only on an **injective** set (two HRESULTs sharing one string
  is defect A2 — an admin whose task had no saved account information was told
  to retype a password forever). The rule binds what the function can
  **return**, not just the table we wrote: the two uncontrolled escapes (Windows'
  own `excepinfo` description and `str(exc)`) are checked with
  `messages.carries_foreign_marker` and dropped for the coded generic. The two
  legitimate owners (`HR_NOT_FOUND`, `HR_ACCESS_DENIED`) return from the table
  **before** the guard, which is why there is no `owned=` knob to forget.
  **Provenance:** Windows' own text for `0x80070520` is literally "A specified
  logon session does not exist. It may already have been terminated." and was
  reaching `interpret_unregister` unscrubbed — measured live 2026-09-16 with a
  faked `com_error` through the real `delete_task` + `interpret_unregister`.
  **Proof-it-took:** three sweeps in `tests/test_task_com.py` — the **table
  sweep** (every row round-trips `interpret_unregister` to exactly the expected
  shape, and only the access-denied row carries that marker), the **description
  sweep** (the two measured Windows strings come back marker-free and carrying
  their hex code, while a marker-free description still passes through), and the
  **injectivity sweep** (value→names over every `MSG_`/`_MSG_` string binding of
  `task_com` / `windows` / `elevated_apply`, exactly one name each). Remove the
  guard and the description sweep is red; give two constants the same value and
  the injectivity sweep is red.

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

- **The `district_domains` validation floor is DIRECTIONAL and must never invert: a BUNDLED config RAISES, a USER-dir config WARNS-and-DROPS (counts only, never the value).** _(Plan 0044 S1, 2026-09-02 · `src/config/loader.py` (`_apply_user_dir_domains_floor`, `resolve_config_path`) + `src/config/models.py` (`is_valid_district_domain`, the ONE spelling of the rule).)_ `district_domains` is a PRESENTATION key the ETL structurally cannot read (`to_raw_dict` emits only `mappings` + `global_config`), so a typo in a hand-edited user-dir file must never kill that district's nightly sync — failing open costs a picker-scoping nicety, failing closed costs the roster. A bundled row is the opposite case: `make validate-config` gates it in CI before release, where a loud failure costs nothing, and the raise is what keeps a pasted personal address out of a public repo. Origin is decided ONCE, from the search-dir INDEX in `resolve_config_path` (user = 0, bundled = 1) — never from `path.parent`, and the legacy single-`config_dir` override is defined as bundled-equivalent (NO floor) because a one-dir search cannot express a tier and that seam exists to fail loudly. The WARN never interpolates an entry: the likeliest bad row IS a personal email address and logs are ops-visible. Do not make the bundled path warn; do not make the user path raise; do not derive origin a second way; do not log the value.

- **An emitted overlay can never name one source-file ROLE two ways: the rename map is keyed by ORIGINAL filename, propagated to every reference including `school_year_sources`, and the emission is REFUSED rather than shipped half-renamed.** _(Plan 0044 S1+S4, 2026-09-03 · `src/config/authoring.py` (`_build_renames`, `_assert_no_divergence`, `OverlaySpec.__post_init__`, `folded_filename`) + `src/ui_flet/config_editor.py` (`distinct_source_files`, `file_form_rows`).)_
  One GDE file is named by up to three entity ROLES (`Classes.student_schedule`, `Enrollments.student_schedule`, `global_config.school_year_sources.student_schedule`, …), and the unit a district admin actually renames is the FILE. So the form is keyed by the file and the emission fans out to every reference. A PARTIAL propagation is the dangerous shape because it **LOADS CLEANLY**: the overlay validates, the run starts, and one entity silently reads a filename that is not in the folder — an empty CSV, not an error. `_assert_no_divergence` therefore checks the built overlay against every reference site and REFUSES the emission rather than writing a half-renamed file, and two companion refusals belong to the same rule: a rename target may not also be another rename's ORIGINAL (a chain re-points the first role at the second file's data with every other guard green), and two files may not collapse onto one name. All three comparisons fold through the ONE `folded_filename` — `b.TXT` and `B.txt` are one file on Windows, and two spellings of "same file" would let the Files step and the emitter disagree about which rows are one row. The honest LIMIT is stated rather than hidden: per-ROLE divergence (one file deliberately named differently for two entities) is INEXPRESSIBLE on this model, and stays a ROADMAP line. Do not weaken the divergence check to admit it, do not key renames by entity/role, do not add a second filename fold, and do not let a refusal degrade into a warning.

- **The creator's resume token and its activation write are SEPARATE and each single-pathed: `creator_pending_sis` is ADVISORY and may never activate anything, and `activate_creator_config` is the ONE writer behind the CREATOR's activation — district and tested digest in ONE save; every OTHER `sis_type` writer reaches a user-authored config only after the same comparison.** _(Plan 0044 S3+S6, 2026-09-03 · `src/config/app_config.py` (`creator_save`, `activate_creator_config`, `_ADVISORY_FIELD_PREFIXES`) + `src/ui_flet/screens/creator.py` (`creator_gate_current`) + `src/ui_flet/config_editor.py` (`activation_allowed`).)_
  The `creator_` PREFIX is load-bearing: it is what puts the family in `_ADVISORY_FIELD_PREFIXES` beside `identity_`/`window_`, and therefore what makes a creator-only save on an UNREADABLE profile REFUSED by the existing `_carries_chosen_settings` machinery instead of overwriting settings nobody could read. `creator_save` refuses every non-`creator_*` key — `sis_type` most of all — so no creator path can become a back door into the one field that decides what the nightly sync converts. The activation is the opposite kind of write and is deliberately its own method: it puts the district AND the digest of the config the passing test ran against into ONE save, so no crash between two writes can leave a district active with no recorded test (activation without evidence) or a test recorded against no district (evidence without activation). `sis_type` has **FIVE** writer surfaces — the wizard's standard District pick, the wizard's creator activation, Mapping's Apply, Mapping's panel activation, and the Settings folders-card Save — and ALL FIVE reduce to ONE comparison for a user-authored config: `config_editor.activation_allowed`, reached through FOUR call sites (`creator.creator_gate_current` for both creator activations, the wizard's District step, the folders card, and Mapping's Apply), so no surface can activate what another refuses. The folders card asks it only when the district CHANGES — a folder-only fix on the district the nightly already runs activates nothing. The fifth surface was found by S6's review and gated in the same slice (commit `1195063`, DECISIONS 2026-09-03): the standard District step lists user-dir rows too. Do not add a second activation path, do not widen `creator_save`'s key rule, do not drop the prefix, and do not split the district and the digest into two saves.

- **The verified fact is DIGEST-keyed and fails SAFE: a refused or failed invalidation still leaves a digest that no longer matches, and a non-matching digest can only re-ASK for a test.** _(Plan 0044 S3+S6, 2026-09-03 · `src/ui_flet/config_editor.py` (`stored_verified_digest`, `verified_is_current`, `activation_allowed`) + `src/config/authoring.py` (`resolved_digest`, `current_digest`).)_
  The fact recorded when a district's test conversion passes is not a boolean "verified" but the digest of the WHOLE RESOLVED config — overlay plus everything it inherits. That makes the dangerous direction structurally unreachable: any edit to the overlay, and any vendor change to the base beneath it, changes the digest, so a stale PASS cannot survive an edit by anyone forgetting to clear a flag. The price is paid in the safe direction — the fact over-expires, and the worst outcome is one extra test run. This is also why an invalidation write that is REFUSED or fails is harmless: the stored digest simply no longer matches what is on disk, which is exactly the state that asks for a test. A BUNDLED config never reads the digest at all (there is nothing an admin could have edited). Do not key it on mtime, file size or app version; do not "repair" a mismatch by re-stamping the digest; do not add a second spelling of the comparison; and do not let a mismatch block anything other than an activation.

---

_The sixteen rows below are the plan 0053 ETL failure policy, one per rule P1–P16. Each is a POINTER: the rule, its Today/Status detail and its cited lines live in `docs/developer/failure-policy.md` (the section named in the row); the decision is `docs/claugentic-DECISIONS.md` 2026-09-23 "failure is scoped to the entity". A row marked **planned** describes a target the code does not yet meet — the named slice makes it true and flips both the row here and its section there, in the same change._

- **P1 — Asymmetric risk: never widen delivered PII (fail CLOSED at the smallest scope containing the fault); never silently shrink or omit a deactivating file because of a DETECTED fault; surplus rows fail OPEN with a recorded signal; optional blanks are recorded.** _(Plan 0053, 2026-09-23 · `failure-policy.md` §1.)_ **Enforced since S4 for scope** — a PII guard fails closed at the smallest scope §3 allows (Family's guardian filter leaves only Family out; a CRITICAL entity's guard still fails the run). **Planned** for the rest (S10 partial ship; S11 signals; D10 the all-Active default): fail-open signals are still log lines only.

- **P2 — Bulkhead: transformers RAISE and never catch to continue at entity scope; exactly ONE entity-scope boundary exists, in `run_transform`, shared by the CLI and Convert; CRITICAL re-raises the original object; ISOLATABLE is recorded, logged once at ERROR with traceback, and skipped; `BaseException` is never caught.** _(Plan 0053 · `failure-policy.md` §2.)_ **Enforced since S4** (`src/etl/pipeline.py` `run_transform`; `tests/test_pipeline_entity_isolation.py`). What must stay true: the bulkhead lives ONLY in `run_transform` — exactly one broad handler there and none under `src/etl/transformers` outside the field-map engine (both AST-pinned), so a second entity-scope decision anywhere else is red; an entity not in `ENTITY_CRITICALITY` is CRITICAL (`criticality_of` — never default an unknown entity to isolatable); only CRITICAL entities publish `TransformContext` state (AST-pinned), which is why no dependent-withholding branch is needed; a failed entity's output is dropped WHOLE — never substituted by a last-good CSV, an unfiltered frame or a partial one — and its previous CSV leaves the delivery glob the same night; a partial run is never green (PARTIAL/WARNING from its own record, every night it persists). A run that BUILT nothing re-raises the first isolated exception rather than reporting `no_output`. Still open: `enrollments.py:159-161`'s narrow catch-to-continue (S10).

- **P3 — Criticality is declared once (`outcomes.ENTITY_CRITICALITY`); an unlisted entity is CRITICAL; an entity another depends on is CRITICAL; promotion needs partner evidence or absence-already-ships evidence, no published context state, no CRITICAL reader, a declared dependency and a DECISIONS entry.** _(Plan 0053, D1 decided 2026-09-23 · `failure-policy.md` §3.)_ **Enforced as a declaration since S2** (`src/etl/outcomes.py`; `tests/test_etl_outcomes.py` pins totality, unlisted ⇒ CRITICAL, nothing ISOLATABLE depended on and — AST — that only CRITICAL entities' modules publish `TransformContext` state; `tests/test_failure_policy_parity.py` ties the §3 table to it). **Enforced as behaviour since S4**: `pipeline.run_transform`'s bulkhead is the one place that branches on it (ISOLATABLE left out and the run completes; CRITICAL fails the run), pinned per entity by `tests/test_pipeline_entity_isolation.py`.

- **P4 — Never substitute a FAILED or EMPTY entity (no last-good CSV, no unfiltered rows, no partial frame, no filter-less mapping); its previous CSV leaves the delivery glob; its absence is reported every run it persists.** _(Plan 0053 · `failure-policy.md` §4.)_ **Enforced** for the archive/manifest half, and since S3 for the READER of the every-run report (a FAILED outcome in a completed run's own record is PARTIAL / WARNING on Home, Run History and Convert); **enforced since S4** for the PRODUCER (the bulkhead leaves a FAILED entity out whole, and its record carries the FAILED outcome every night — the Unity plain-report contract fixture and the two-night test); **planned** for the partial frame (S10).

- **P5 — A missing column is handled by what it GUARDS: (a) PII scope and (b) join key fail CLOSED, typed, never a raw `KeyError` or partial result; (c) contract field excludes and counts; (d) optional field blanks, warns once, notes; (e) safety heuristic fails open only toward surplus, always recorded.** _(Plan 0053 · `failure-policy.md` §5 + its site catalogue.)_ **Planned (S6, S9, S10, S11 per site)**; S1 typed §5 #1–#4 (`SourceSchemaError`), and since S4 each fails closed at its entity's declared scope (class (a) `ENFORCED`). Four sites already conform (§5 #7, #20–#22; #22 excluding its evidence reader #22a).

- **P6 — Every boundary-crossing ETL failure is a typed `EtlError` with a bounded category; classification is by `isinstance` only, never message text; every closed-enum member maps to copy, a verdict and a doc row.** _(Plan 0053 · `failure-policy.md` §6.)_ **Enforced** for the taxonomy and type-only classification since S1 (`src/etl/errors.py`; `tests/test_etl_errors.py` AST-pins the classifier); **enforced** since S3 for copy — `src/ui_flet/failure_copy.py` is TOTAL over every category (NONE raises), every valid (kind, reason) and every kind, pinned from the enums and against `failure-policy.md` §6; **planned** for the still-untyped raw-`KeyError` sites (S10).

- **P7 — Level-triggered: every configured entity has exactly one outcome per run, and a FAILED outcome in a successful run is PARTIAL / WARNING every run it persists, derived from that run's own record — never from a baseline the run archives.** _(Plan 0053 · `failure-policy.md` §7.)_ **Enforced since S2 for the first half** — every configured entity has exactly one outcome per run in the record's `entity_outcomes` (`OutcomeLedger.complete()` refuses a gap; `tests/test_pipeline_run_store.py`). **Enforced since S3 for the reader of the PARTIAL/WARNING half** — `home_status.LatestReason.PARTIAL` (below FAILED_DELIVERY, above ANOMALY), derived from the record's own `entity_outcomes` (a delivery-only record inherits its build's through the REQUIRED `prior_build`), identical on Home, Run History and Convert. **Enforced since S4 for the producer** — the bulkhead's FAILED outcome rides a completed run's record, so the warning persists every night the entity stays out, while the vanished-entity anomaly fires only the first night (measured: `tests/test_pipeline_entity_isolation.py`'s two-night test).

- **P8 — Symmetric sinks: every build attempt on every entry point writes exactly one record through `build_run_record`; outcome, status and category parameters are required keyword-only; a convenience default is allowed only where it errs toward MORE warnings.** _(Plan 0053 · `failure-policy.md` §7.)_ **Enforced since S2 for the parameter half** — `entity_outcomes` is REQUIRED keyword-only with no default on `build_run_record`, `run_transform` (`ledger`), `convert._record_manual_run`, `PipelineResult` and `ConvertResult` (signature pins in `tests/test_etl_outcomes.py`). **Enforced since S5 for the rest** — every Convert attempt writes exactly one record: a raising config load (typed `ConfigLoadError`, `config`; any other load raise by its type) and any raise from `to_raw_dict` through the quality report (one reasoned broad sink, closing before the SFTP leg, category by TYPE, completed ledger, the SAME object re-raised) go through `convert._record_failed_attempt`; `_record_manual_run`'s `status`/`error_category` are required too. Four deliberate non-records (unset output folder, unusable output folder, `NO_INPUT`, `NEEDS_ANOMALY_ACK`; DECISIONS 2026-09-24). Pinned by `tests/test_convert_failure_record.py`. What must stay true: the sink re-raises bare and never widens over the delivery (AST-pinned), and recording can never mask the fault. A failed MANUAL record never sets Home's verdict (owner decision D14, `home_status.verdict_records` — one filter for Home, the Run History banner and the Setup badge's probe timestamp).

- **P9 — Bounded surfacing: records, banners and cards carry closed-set codes humanised through total copy tables — never `str(e)`, cell values, paths or OBSERVED header text; config-DECLARED labels only, membership-validated and sanitised (D4).** _(Plan 0053, D4 decided 2026-09-23 · `failure-policy.md` §8.)_ **Enforced** for the category-only store and fixed copy — since S3 through the total tables in `src/ui_flet/failure_copy.py`, whose only variable text is an authored entity phrase and counts (sentinel-swept); **enforced** since S1 for the four sites that dumped observed headers into exception/log text (count only; sentinel-pinned); **planned** for labels (S7).

- **P10 — One column resolver (`columns.resolve_source_column`) with one shape policy built on `models.classify_field`; presence via `columns.require_columns`; new shared behaviour goes in composed modules, not `BaseTransformer` methods.** _(Plan 0053 · `failure-policy.md` §9.)_ **Planned (S9).** Today three resolvers disagree on a bare string.

- **P11 — Source observation never enforces: it attributes missing mapped columns per entity over that entity's OWN files, never raises, never gates, and keeps preflight's soundness rule.** _(Plan 0053 · `failure-policy.md` §10.)_ **Planned (S6).** Today preflight runs only from the creator and merges every file's headers.

- **P12 — Additive record evolution: new facts are new JSON keys; no `runs` DDL, CHECK or `user_version` change until the ROADMAP migration-ladder item is fixed; readers are TOTAL; an unknown kind/reason from a newer build reads as FAILED/TRANSFORM_ERROR.** _(Plan 0053 · `failure-policy.md` §7.)_ **Enforced** for no-DDL and total readers, including the outcome reader since S2 (`outcomes.outcomes_from_record`: never raises; unknown kind/reason → FAILED/`transform_error`).

- **P13 — A policy that depends on SpacesEDU import behaviour cites a dated confirmation row in `output-contract.md` or the open question (Q5); until confirmed the conservative branch applies, and partner docs never claim an omitted file has no side effects.** _(Plan 0053 · `failure-policy.md` §7 (partner-assumption row) + §3 footnote; `docs/developer/output-contract.md` Q5, `Q5-status: open`.)_ **Enforced by review** from S0, and **mechanically since S4**: `tests/test_failure_policy_parity.py` ties the FAQ's criticality bullets to §3's sets and its "pending confirmation" clause to `Q5-status` (present exactly while `open`), with doctored-FAQ and doctored-contract twins.

- **P14 — Docs follow code and are pinned: every rule row carries `ENFORCED` or `PLANNED (0053 Sn)`; a slice flips its own rows in the same change; every table mirroring a code constant has a parity test with a non-vacuity assertion and a doctored-doc twin.** _(Plan 0053 · `failure-policy.md` §0.)_ **Enforced since S2 for the two code-mirroring tables** (§3 criticality, §6 vocabularies — `tests/test_failure_policy_parity.py`, with non-vacuity and doctored-doc twins); the Status column and every other row are still held true by review. Since S4 the parity test also pins the partner FAQ (criticality bullets ↔ §3, pending clause ↔ `Q5-status`); S11 extends it to the site tags.

- **P15 — Config typos are loud and origin-keyed: a bundled config RAISES at load; a user-dir overlay WARNS at run and is REFUSED at authoring; `Field*` leaf models forbid extras everywhere; the root stays `extra="ignore"`.** _(Plan 0053 · `failure-policy.md` §6 (config-typo row).)_ **Planned (S12).** Same direction as the `district_domains` floor above — never invert.

- **P16 — Layering: `src/etl`, `src/config`, `src/history`, `src/quality` never import flet or `src.ui_flet`; an explicit `FLET_FREE_MODULES` list never imports flet; transformers never import `pipeline`; every in-scope `except Exception` carries a reasoned `noqa: BLE001`.** _(Plan 0053 · `failure-policy.md` §11.)_ **Planned (S13a).** True by inspection today, unpinned.
