# 0049 — Machine-scoped install + gMSA principal (plan 0046's parked half, unparked)

- **Status:** APPROVED 2026-09-17 (owner, in session) as the v2 design; **rebased here onto v3.21.0** (`8f5761e`) after the rebase found plan 0046 B/C/D and plan 0047 A1/A2 already shipped — see *Rebase notes*. **S-1a-i in build since 2026-09-17** (branch `claude/0049-s1a-foundation`; the S-1a spec is the `## Spec` section below — it lands as two PRs, S-1a-i then S-1a-ii).
- **Roadmap item:** discharges *"Service-account principal support beyond a named account + password — gMSA / SYSTEM / … — and the machine-scope secret storage those principals would need"* (the gMSA half; SYSTEM/LOCAL SERVICE/NETWORK SERVICE stay parked — nobody asked). Also discharges two Slice-C residuals it names as "surfaced, not solved": the run-history gap under a foreign principal and **A9** (the seasonal window not enforced for a foreign principal), and retires Slice B's documented one-time `runas … --sftp-configure` step.
- **Supersedes:** the 2026-09-16 DECISIONS entry *"gMSA / SYSTEM / LOCAL SERVICE / NETWORK SERVICE are PARKED"* — its own condition was *"until explicitly asked for"*; on 2026-09-17 SD60's security team named gMSA as their method in use and stated the requirement (a scheduled task under an account they specify, no password in the UI). **What that evidence does NOT prove:** that gMSA registration succeeds on their host — nothing here claims it does until a district reports a green nightly.
- **Predecessors:** plan 0046 (Slice 1 engine refusal · B service-account UI · C signal honesty · D docs — all landed v3.21.0), plan 0047 (diagnosability — landed), `.claude/plans/0046-HANDOVER.md` §5–§6 (the verified facts and the preserved gMSA research; do not re-derive).
- **Review:** the v2 design went through a 4-lens adversarial panel (security · resilience · YAGNI · product; 40 findings, 2026-09-17). Every design-changing finding and its disposition is in `## Review record`. The panel's refuter stage was mis-specified (it refuted *proposed* code for not existing) and its verdicts were discarded.
- **Owner decisions (2026-09-17, in session; do not re-litigate):** gMSA ships in Settings only, with an on-screen "not yet tested against a live domain" caption, no feature branch · live proof is the owner's manual walk on their domain-joined laptop (no CI live smoke) · another administrator on a machine-scoped install is auto-granted on first launch (one UAC), identity is per-install · machine-scope foundation lands before the gMSA UI; the engine principal model lands after the shipped service-account path is machine-scoped.

## Rebase notes — what v3.21.0 already did, and what that changes

| v2 assumed | v3.21.0 shipped | Consequence here |
|---|---|---|
| Slice A (0x80070520 copy etc.) unbuilt | Plan 0047 A1/A2 landed: six canonicals incl. `MSG_NO_LOGON_SESSION`, kind-agnostic classifier, `[HRESULT` log anchor, cross-SID copy, "try again" retired | **Dropped.** The GPO branch gains one pointer to the gMSA path (S-4); `test_partner_doc_schedule_copy_parity.py` moves with it |
| "(c) a different Windows account" UI to be built in a Settings disclosure | 0046 B: an editable, prefilled **Windows account for the nightly task** box in the Daily schedule section (wizard AND Settings), typed value sent verbatim, `RegisteredSchedule.run_as_user` recorded, in-place switch REFUSED → Remove → Schedule (owner A10) | **(c) exists.** This plan makes it machine-scoped: provisioning triggers at Schedule time when the sent account is foreign; the ACE is pruned at Remove time. No additive-then-prune inside one op (R4 discharged by A10) |
| `_run_as_account` rename | Done (`_keyring_owner_account`, 8 call sites; `test_the_old_name_is_gone`) | Dropped |
| Slice C signal honesty to be *fixed* | 0046 C **swapped** the alarms: `ScheduleStatus.foreign_account` suppresses `_is_contradiction`/`_is_missed_run`, `FOREIGN_RECORDS_NOTE` says records live elsewhere, `sync_window_foreign_note` says the pause is not in force | Under machine scope those statements become FALSE (records and config are shared). S-2 adds `shared_records` so the predicates and notes branch honestly |
| Explicit `PrincipalKind` deferred forever | 0046 Revision: *"DEFERRED … revisit when a third kind arrives"* | gMSA is the third kind → D1 is due, per that entry's own condition |
| Docs from scratch | 0046 D: `headless-sftp-setup.md` §"Running the nightly sync as a service account" (the `runas … --sftp-configure` step, "why Run History goes quiet", seasonal-window limitation, rotation) | S-2 rewrites that section: the step is retired on a machine-scoped install; the two limitations are gone |
| S0 unmeasured | Measured 2026-09-16: a batch-logon task reads its OWN keyring | Irrelevant to gMSA (cannot seed its own); still true for the per-user path, which stays byte-identical |

Byte-identical at v3.21.0 vs v3.20.0 and therefore designed against correctly: `src/utils/paths.py`, `src/sftp/uploader.py`, `src/history/store.py`, `elevation.py`'s DPAPI/handshake, `app_config.py`.

## Problem

The task's argv survives an account change (`windows.py:_build_action_args`) but **everything else is per-user**: `paths.user_data_dir()` anchors to the caller's `%LOCALAPPDATA%` on every branch. A foreign principal gets a blank profile: `AppConfig.load()` finds no `config.json` → `sftp_is_configured()` False → `_sftp_upload` returns False before touching the keyring → **exit 3 nightly**; its `history.db`/log land in *its* profile; the zip is staged in *its* `%TEMP%` (`uploader.py:575`); the seasonal window reads *its* (default) config. Slice B/C/D made all of that honest and documented — but honest is not fixed, and for a **gMSA it is fatal**: the SFTP password is per-user Credential Manager, a gMSA cannot log on interactively, so the documented `runas … --sftp-configure` seed is impossible. Engine: `apply_definition` infers logon type from `password is not None` (no NULL-password + `TASK_LOGON_PASSWORD` shape); `_RUN_AS_USER_RE` rejects `$`; the elevation predicate is `has_password and not is_elevated()` (`windows.py:475`) — a passwordless gMSA would take the unelevated path and fail `0x80070005` (measured); `TaskFacts`/`ScheduleReadback` carry no principal.

## Goals / Non-goals

**Goals.** (1) A machine-scoped install — settings, run history, log, secret — that every principal on the box reads, discovered through an admin-only-writable switch, provisioned inside the elevation Schedule already performs. (2) An explicit principal model in the engine with unsafe combinations unrepresentable. (3) A gMSA option in Settings, honestly labelled. (4) The 20 existing installs byte-identical: nothing turns machine scope on but an admin scheduling a foreign account.

**Non-goals (stated).** SYSTEM / LOCAL SERVICE / NETWORK SERVICE · Linux/macOS machine scope · un-provisioning (ROADMAP) · a drift *verdict* (display only) · a machine-local admin group · verifying the principal's own folder access (the first nightly's exit code + `run_as` on the record tell the truth; docs say the app cannot check) · SSH key auth (same DACL, *less* machine-bound than DPAPI, plus a server-side change to the password-provisioned per-district SFTP recipe — ROADMAP as rotation hygiene) · an app-orchestrated principal switch (owner A10 stands) · a CI live smoke (owner) · UPN account form (ROADMAP, zero demand) · a Windows service · an installer.

## Design

### D0 — Two facts, read once
- **Machine scope is a switch, not a discovery.** `HKLM\SOFTWARE\DistrictSync` → `MachineScope` (DWORD 1) + `ProvisionedAt`/`ProvisionedBy` (display only). World-readable, admin-only-writable, so a standard user can pre-create nothing that redirects the app — this replaced the v2 marker-and-owner-check discovery, because `C:\ProgramData`'s default ACL (`BUILTIN\Users:(CI)(WD,AD,WEA,WA)` + `CREATOR OWNER:(OI)(CI)(IO)(F)`, read on the owner's machine) lets any standard user pre-create and own `C:\ProgramData\DistrictSync`. Written ONLY by the elevated `provision` op, as its **commit point**.
- **Resolved once per process.** `paths.pin_data_dir()` at entry (`launcher.py` beside `migrate_legacy_data_dir`; `main._cli`), memoised lazily on first call; `reset_data_dir_pin()` for tests and the post-provision reload. `user_data_dir()`'s answer cannot change mid-run. The startup banner logs dir + scope.
- Ladder: (0) `DISTRICTSYNC_DATA_DIR` wins outright [unchanged] → **(1) win32 ∧ switch on:** `machine_data_dir()` (= `platformdirs.site_data_dir("DistrictSync", appauthor=False)` → `C:\ProgramData\DistrictSync`) MUST exist, be owned by `BUILTIN\Administrators`/`SYSTEM` (ctypes `GetNamedSecurityInfoW`, house pattern) and not be a reparse point — else **`RuntimeError` naming the path**: never fall through (a switch pointing at a missing/untrusted dir is a support case, not a fresh install) → (2)(3)(4) per-user ladder unchanged. `paths.is_machine_scope() -> bool` is the ONE predicate consumers branch on. `paths.handshake_dir()` = the per-user platform dir ALWAYS — the elevation request/result files (`elevation.py:242-259`) are a per-session artefact that must never follow the move into a directory another principal can write.

### D1 — Principal model (engine; S-3)
- `task_com.RegisterParams` gains `kind: PrincipalKind`; **delete** the `password is not None` inference in `apply_definition` (`task_com.py:227-282`).
  - `INTERACTIVE_TOKEN` — user == current, password `None`, `TASK_LOGON_INTERACTIVE_TOKEN`, RunLevel LUA. Today's no-password path, byte-identical.
  - `PASSWORD` — any account, password **required**, `TASK_LOGON_PASSWORD`, RunLevel per `run_highest` (stays HIGHEST for unattended — DECISIONS 2026-09-16 A8). Today's password path.
  - `MANAGED_SERVICE_ACCOUNT` — `RegisterTaskDefinition(..., userId="DOMAIN\name$", password=None, TASK_LOGON_PASSWORD)`, RunLevel per `run_highest`. **Not** `TASK_LOGON_SERVICE_ACCOUNT` (5): verified first-hand (handover §6) that with a domain `UserId` the `<LogonType>` element is silently dropped from the serialized XML.
  - Refused in `__post_init__` (unrepresentable, never defaulted): PASSWORD without password · MSA with a password · INTERACTIVE_TOKEN for a different user · MSA name without `$` · PASSWORD name with `$`. `TASK_LOGON_S4U` stays undefined.
- `validators.validate_gmsa_account(value)` — a **shape check, and the docstring says so**: trailing `$` mandatory; sAMAccountName charset; `DOMAIN\` optional (UI caption "usually `DOMAIN\name$`"); no length rule; **refuses by name** the local computer account (`<COMPUTERNAME>$`, case-insensitive) and any `NT AUTHORITY\*`/well-known form, in BOTH halves — a computer account has the gMSA shape and `THISHOST$` is SYSTEM-equivalent. `validate_run_as_user` unchanged (still rejects `$`; its docstring's N1 sentence updated).
- `windows.register_task`: `run_as_user`/`run_as_password` → `principal: Principal(kind, user, password)` (one caller, `screens/setup.py:2341`; the `Scheduler` Protocol + `WindowsTaskScheduler`/`CronScheduler` adapters follow — cron refuses MSA loud). Elevation predicate (`windows.py:475`) → `kind is not INTERACTIVE_TOKEN and not is_elevated()`. `_MSG_ACCOUNT_NEEDS_PASSWORD` stays for PASSWORD kind. Every new refusal goes through `_fail` (the AST funnel guard in `test_scheduler_runas.py` sees only literal `return False, …`; do not hand it one).
- `elevated_apply` payload carries `kind`; `_do_register` re-validates by kind (same fail-closed ladder). New canonicals, if any, obey `messages.py` (injective, secret-free, no foreign marker) — `test_task_com.py`'s injectivity sweep is the pin.
- Read-back: `TaskFacts`/`ScheduleReadback` gain `run_as` + `logon_type` (`read_task` reads `Definition.Principal.UserId/LogonType`) for **display**. A requested-vs-read-back mismatch is a logged WARNING, fail-open on any spelling it cannot normalise. `foreign_task_account(app_config)` keeps reading the RECORD (`schedule_run_as_user`), as Slice C wired it.
- gMSA failure taxonomy stays unmapped until measured; the `[HRESULT` log anchor + the kind-keyed classifier branch (D6) carry it. A mistyped gMSA name already lands on `HR_NONE_MAPPED` (0x80070534, measured M1).

### D2 — The machine-scoped directory
- Layout: root holds `config.json`, `sftp_secret.bin`, `mappings/`, `known_hosts` (read-only to the principal); `runs/` holds `history.db` (+sidecars), `etl_tool-<sanitised account>.log` per writer (two writers never contend on one rotating file), `tmp/` for zip staging (replaces `%TEMP%`, which follows the principal's profile and its documented load races).
- DACL (`/inheritance:r`): root — `SYSTEM:(OI)(CI)F` · `Administrators:(OI)(CI)F` · `<setup user>:(OI)(CI)M` · `<principal>:(OI)(CI)RX`; `runs/` — additionally `<principal>:(OI)(CI)M`. Least privilege: the service account reads what it needs and writes only run artefacts; it cannot alter the admin's trust-bearing state. The setup user is granted **explicitly** because `BUILTIN\Administrators` in a DACL grants nothing to a non-elevated process (UAC filtered token). `mappings/`+`known_hosts` stay in scope: a self-service district's overlay (plan 0044) lives in `user_mappings_dir()` and the nightly must find it.
- `AppConfig` on the shared `config.json`: `RegisteredSchedule`'s atomic three-facet invariant holds unchanged — the only writer is still the admin's UI (the principal has RX). `identity_*` become per-install (owner's intent: the domain is the district's; the next admin sees the address in Settings and can change it).

### D3 — The secret
- `src/sftp/secret_store.py`: `UserSecretStore` (today's keyring, unchanged) and `MachineSecretStore` — `sftp_secret.bin`, DPAPI `CryptProtectData` with `CRYPTPROTECT_LOCAL_MACHINE` + app entropy, sealing **`{host, username, password}`** so a username/host change invalidates it exactly as the keyring key does today. Both expose `has_secret(host, username) -> bool`.
- **Select rule:** `is_machine_scope()` ⇒ machine store ONLY; otherwise (per-user, legacy, AND `DISTRICTSYNC_DATA_DIR` override) ⇒ keyring ONLY. Never both, never a fallback chain. Tests reach the machine store through monkeypatched `is_machine_scope`/`machine_data_dir` seams.
- **Threat-model statement, distinct from the 2026-06-25/D5 rejection of LocalMachine for the elevation handshake** (that blob crosses a privilege boundary within ONE user and CurrentUser is its SID binding — still correct): the machine store's purpose IS cross-account readability, its confidentiality boundary is the NTFS DACL, and it is selected only on an install the admin explicitly chose to share. `elevation._dpapi` stays CurrentUser-only; the machine store gets named `protect_machine_blob`/`unprotect_machine_blob` with the LocalMachine flag hardcoded inside; the shared private ctypes helper takes `flags` as a **required** argument.
- **Writes are verify-or-destroy:** write → unprotect → compare; on any failure delete the blob and report "delivery is now unconfigured — save your delivery password again". A stale secret is unrepresentable, like a stale selection.
- `AppConfig.sftp_is_configured()` on a machine-scoped install additionally requires `store.has_secret(host, username)` — "configured" means the selected store answers (`pipeline._sftp_upload` and `main --sftp-*` route through it).
- **Posture, stated for the district docs:** protected to local administrators, SYSTEM and the service account on this computer; **a file-level or image backup carries it** (the machine DPAPI key lives in the offline hives) — exclude `C:\ProgramData\DistrictSync\sftp_secret.bin` from backups or treat them as sensitive; the compensating control is that the SFTP password is per-district and rotatable. Never claim "useless off-box".

### D4 — Provisioning (elevated child; new op `provision`, fires at Schedule time when the sent account is foreign)
Pre-UAC gates in the parent (extend `setup_gates.register_block`; no permissive defaults):
- SFTP: configured ∧ secret readable from the keyring → seed; not configured → proceed with no blob and no claim; **configured ∧ unreadable → `RegisterBlock.DELIVERY_SECRET_UNREADABLE`** with a named banner and a route to the Delivery section.
- Folders: input or output under any user profile (`C:\Users\…`) or on a mapped drive letter → `RegisterBlock.FOLDER_NOT_SHAREABLE`, naming the folder.
- Explicit confirm on three sentences BEFORE UAC: what moves; that other administrators of this computer can then open DistrictSync and take over these settings; that this version cannot move them back.

Elevated sequence, fail-closed; the result carries a **fixed vocabulary of step identifiers + icacls exit code only** (never stderr, never a secret; the existing non-leak tests extend to BOTH secrets across result/message/log):
1. Path exists? → it must already be a valid machine profile (owner Admins/SYSTEM, not reparse, switch on) or **refuse and name the path** — never `/setowner` or adopt a directory we did not create.
2. Create → strip inheritance immediately → verify **empty and not a reparse point** (a standard user can drop into a fresh ProgramData subfolder between mkdir and icacls; a surprise is a refusal + removal, not a race to lose) → apply the D2 DACL; `/setowner Administrators`.
3. Already provisioned (a later Schedule after Remove): grant the new principal; the prior one was pruned at Remove.
4. Migrate: `config.json` parse-then-write; `history.db` via `sqlite3.Connection.backup()` into staging, then `PRAGMA integrity_check` + row-count compare before promote (never `copytree` a live WAL db); `mappings/`, `known_hosts` copied. Own function; `migrate_legacy_data_dir` untouched.
5. Secret: write verify-or-destroy.
6. **Commit:** write the HKLM switch. Failure before this → remove the directory we created and report the step; failure after → leave the switch (the dir is valid), report.
7. `task_com.register_task_definition(params)` → confirm read-back (existence, as today; `_confirm_registration`).

Parent, after `switch_committed=True` (regardless of `registered`): `reset_data_dir_pin()` → re-pin → reload `AppConfig` → re-point the log sink to `runs/etl_tool-<user>.log` → rename per-user `config.json` and `history.db`(+sidecars) to `*.pre-machine-<ts>` (they were copied; a stale writer must fail loudly, not diverge; WARN on rename failure — the ladder never reads them) → `MOVED.txt`. Cross-SID consent is unchanged (`DSYNC_DIFFERENT_ACCOUNT`, nothing provisioned). **Remove nightly sync** on a machine-scoped install additionally prunes the recorded principal's ACE (new `prune_principal` leg on the existing elevated delete); the secret stays (Convert's manual delivery needs it).

### D5 — Other administrators, switching
- **Auto-grant on first launch.** Switch on ∧ access denied on the machine dir → one single-purpose screen (`screens/grant_access.py`): *"DistrictSync's settings on this computer are shared. Windows needs to approve your account once."* → UAC → new elevated op `grant_current_user` (one additive `:M` ACE; nothing else) → re-pin → enter the app. A non-administrator sees Windows' credential prompt; declining → a loud, named error. The launch page never re-gates (identity is per-install).
- Switching principal = owner A10: Remove (prunes) → Schedule (provisions/grants). Scheduling (a)/(b) again on a machine-scoped install is **allowed** (the setup user holds `:M`; copy: "shared settings on this computer; the nightly runs as your account"); the scope is shown **durably** on Settings and Home ("Settings for this computer" / "for your account only"). Un-provisioning → ROADMAP, named in the confirm copy.

### D6 — UI (S-2 machine-scopes the shipped path; S-4 adds gMSA in Settings only)
- **Shipped field stays.** `_SERVICE_ACCOUNT_DELIVERY_NOTE` (`setup.py:239-245`, the on-screen `--sftp-configure` step) becomes scope-aware: on a machine-scoped install it reads "your delivery password is on this computer where `<account>` can read it — nothing else to set up"; on a per-user install it is unchanged (still true there).
- The delivery-password line (`setup.py:2903`, "readable by `<keyring owner>`") gains **four forms derived from read-back + scope, never from a requested value**: LIVE machine → "saved on this computer where `<account>` can read it"; LIVE/none user → today's keyring wording (unchanged); MISSING/UNKNOWN on machine scope → "saved on this computer for whichever account runs the nightly — no nightly is scheduled right now"; no readable credential → a WARNING that delivery will not run. `test_ui_flet_service_account.py`'s verbatim pin moves deliberately (a positive twin per form).
- **Slice C made honest again:** `derive_schedule_status(..., foreign_account, shared_records)` (new required kwarg from `probe_schedule`, sourced from `is_machine_scope()`): `_is_contradiction`/`_is_missed_run`/`_foreign_records_elsewhere` suppress only when `foreign_account and not shared_records`; `FOREIGN_RECORDS_NOTE` gets a shared-records sibling ("runs as `<account>`; its run records are here"); `sync_window_foreign_note` fires only when NOT shared (on a machine-scoped install the pause IS enforced — `main._cli` reads the shared config — so the note must not deny it). `test_ui_flet_foreign_principal_wiring.py`'s AST pins extend to the new kwarg.
- Scope line on Settings + Home. Run record gains `run_as` (additive column, `user_version` 2); Run History shows which account a night ran as.
- **gMSA (S-4, Settings mount only, never the wizard):** beneath the account field, a disclosure *"This is a group Managed Service Account (gMSA) — no password"* that switches the field's validator to `validate_gmsa_account`, hides the password field, and shows: the caption **"Not yet tested against a live domain. If it does not work, we will need the code shown on screen and your IT team's help."**, the three IT prerequisites, and the partner-doc link. The wizard's field keeps rejecting `$` as today.
- Errors, keyed on **kind**: MSA → headline "Windows would not schedule the task as `<account>`", the prerequisites as a checklist, the code on screen (`_code(msg)`, already selectable) and explicitly no "try again". The `MSG_NO_LOGON_SESSION` branch gains one sentence pointing at the gMSA disclosure (Settings). `test_partner_doc_schedule_copy_parity.py` and `docs/partner/troubleshooting.md` move together.

### D7 — `--diagnose` (read-only, secret-free; the verification instrument)
Prints: resolved data dir + scope + HKLM values; machine-dir owner/reparse/DACL summary; secret store selected + `has_secret` + identity match (never the value); schedule read-back incl. `run_as`/`logon_type`; last run record. The evidence the owner's manual walk produces; a PII-free "run this and send the output" for support.

## Slices (each lands complete, in this order; docs ride the slice that makes them true)

| # | Slice | Serves | Rides along |
|---|---|---|---|
| S-1a | Foundation, **inert**: D0 ladder + HKLM read + memo + `is_machine_scope` + `machine_data_dir` + `handshake_dir` pin · secret store (both impls, select rule, identity binding, `has_secret`) · `uploader`/`pipeline`/`main --sftp-*` scope-aware · zip staging under `runs/tmp` when machine-scoped · per-writer log names under `runs/` · store `run_as` column · **a `windows-latest` pytest leg in `ci.yml`** (today's `WINDOWS_ONLY` real-DPAPI tests never run in CI) | foundation (nothing can switch it on) | ARCHITECTURE_TREE; DECISIONS: LocalMachine threat model |
| S-1b | Provisioning: `provision` + `grant_current_user` + `prune_principal` ops · migration function · post-provision session steps · auto-grant screen · `--diagnose` | foundation | `headless-sftp-setup.md` posture statement; DECISIONS: parking reversed |
| S-2 | Machine-scope the shipped path: pre-UAC gates + confirm · scope-aware delivery note + four-form line · `shared_records` through Slice C's predicates/notes · scope line on Settings/Home · `run_as` in Run History | **SD54 without the manual step; A9 + run-history gap fixed** | `headless-sftp-setup.md` §service-account rewrite; `installation.md`; CHANGELOG |
| S-3 | Principal model engine (D1) — today's callers byte-identical | gMSA foundation | validators docstring |
| S-4 | gMSA disclosure (Settings only) + untested caption + MSA error branch + GPO-branch pointer · `ScheduleAccountFacts` gains `kind` so `register_block` never fires `ACCOUNT_NEEDS_PASSWORD` for an MSA, and `schedule_reconcile` / the `downgrade_interrupt` service-account variant get MSA copy that names no password (`principal_key` / `foreign_task_account` are name-keyed already — verified 2026-09-17, no change) | **SD60** (next normal release) | `troubleshooting.md` + parity test; CHANGELOG "available, untested" |

Owner's manual walk after S-2 (domain user, PASSWORD kind) and after S-4 (gMSA) — `## Verification`. SD60 tests after S-4 ships.

## Affected files
Engine: `src/scheduler/task_com.py` · `windows.py` · `elevated_apply.py` · `elevation.py` · `__init__.py` · `messages.py` (only if a new canonical is added) · `src/utils/validators.py`
State: `src/utils/paths.py` · `src/config/app_config.py` · `src/history/store.py` · `src/utils/logger.py` · `src/ui_flet/launcher.py` · `src/main.py`
Secret: `src/sftp/secret_store.py` (new) · `src/sftp/uploader.py` · `src/etl/pipeline.py`
UI: `src/ui_flet/setup_flow.py` · `setup_gates.py` · `screens/setup.py` · `setup_errors.py` · `schedule_status.py` · `schedule_probe.py` · `home_status.py` · `run_history.py` · `screens/home.py` / `run_history.py` / `shell.py` (the AST-pinned `probe_schedule`/`sync_window_paused` call sites) · new `screens/grant_access.py`
Tests: `test_paths.py` · `test_app_config.py` · `test_task_com.py` · `test_scheduler_runas.py` · `test_scheduler_elevation.py` · `test_elevated_apply.py` · `test_ui_flet_service_account.py` · `test_ui_flet_foreign_principal_wiring.py` · `test_ui_flet_schedule_status.py` · `test_ui_flet_home_status.py` · `test_partner_doc_schedule_copy_parity.py` · new `test_secret_store.py`, `test_provision.py`, `test_diagnose.py` · `.github/workflows/ci.yml`
Docs: `docs/partner/headless-sftp-setup.md` · `installation.md` · `troubleshooting.md` · `docs/claugentic-DECISIONS.md` · `ROADMAP.md` · `ARCHITECTURE_TREE.md` · `CHANGELOG.md` · `CLAUDE.md` (one dense paragraph: the switch, the ladder rule, the select rule, the pinned kwargs)

## Reuse (found, not re-invented)
`elevation.write_request/read_result` + `run_elevated` (op-agnostic) · `elevated_apply` refusal ladder + `{ok,message}` contract · `_set_owner_only_dacl` icacls pattern + `system_binary` pin · `_fail` logging funnel + `[HRESULT` anchor · `messages.py` marker vocabulary · `setup_gates.register_block`/`RegisterBlock` · `principal_key`/`foreign_task_account` · `ScheduleStatus.foreign_account` carrier · `run_result_verdict` · `migrate_legacy_data_dir`'s stage-then-promote *technique* · `tests/test_scheduler_elevation.py` real-DPAPI pattern · `tests/test_paths.py` `data_dirs` fixture + `real_user_data_dir` marker · `conftest.isolated_user_profile`.

## Verification
**Gates (venv python, never `make`; always with `DISTRICTSYNC_DATA_DIR` set):** pytest w/ 80% cov · ruff check + format --check · mypy (non-UI) · bandit `-c pyproject.toml` · tree-check · `check_no_emails.py` · validate-config inline · **CI's own `test pass` line read and quoted**; the owner merges.

**Unit (every OS, mocked seams):** every unrepresentable `PrincipalKind` combination refused · elevation predicate per kind · ladder: switch-on ∧ untrusted → `RuntimeError` (positive twin: switch-on ∧ trusted → machine dir) · memo pin/reset · select rule (machine ⇒ machine only; override/user ⇒ keyring only; never both) · identity-bound secret invalidates on username/host change · `has_secret` feeds `sftp_is_configured` on machine scope · verify-or-destroy leaves no readable blob · `provision`: refuse pre-existing untrusted path; non-empty-after-create refusal; rollback before commit removes the dir; result vocabulary carries no secret / no stderr (non-leak table extended to BOTH secrets) · migration: live-WAL backup + integrity check; parse-then-write config · pre-UAC gates · four-form delivery line · `shared_records` truth table across the three predicates + both notes · kind-keyed error branches · Slice C wiring pins extended, not bypassed.

**Windows-only real (new CI leg + local):** DPAPI LocalMachine round trip + assertion the LocalMachine flag reached `CryptProtectData` · DACL apply-then-`icacls` read-back on a tmp dir (Users absent; principal RX at root, M on `runs/`; owner Administrators).

**Owner's manual walk on the domain-joined laptop (outcome → DECISIONS; `--diagnose` output is the evidence):**
1. Ask IT for (a) a domain user with local-admin on the laptop; (b) a gMSA with the laptop in `PrincipalsAllowedToRetrieveManagedPassword`, `Install-ADServiceAccount` run there, and "Log on as a batch job" granted. Ask whether the laptop's GPO sets the credential-storage policy — if so, step 2 reproduces SD60's `0x80070520` live (and verifies 0047's copy).
2. After S-2: as today's local account, delivery on → Daily schedule → type the domain user + password → confirm → UAC → `--diagnose` (switch on, dir trusted, secret present, `run_as` read back) → trigger the task → Run History shows the record with `run_as`; delivery reached the host (or exit 3 with an auth failure, which still proves the secret was read).
3. Sign in as the domain user → auto-grant screen → UAC → app enters with the shared settings; Settings shows the previous identity address with Change.
4. After S-4: Settings → the gMSA disclosure → the untested caption → register → `--diagnose` → trigger → record. Any HRESULT is mapped afterwards, from evidence.
5. Hand SD60 the release; their green nightly (date + district) is what flips the caption.

## Review record (v1 → v2, 2026-09-17; carried into this rebase unchanged)
Panel: security (S), resilience (R), YAGNI (Y), product (P). Refuter stage discarded (mis-specified). Dispositions of every design-changing finding:

| Finding | Disposition |
|---|---|
| S1/S8 pre-created ProgramData dir laundered by `/setowner`; `created_now` never converges | **Accepted → D0/D4:** HKLM switch replaces marker discovery; refuse any pre-existing untrusted path; create→strip→verify-empty; switch is the commit point |
| S2/R-cut a marker under `DISTRICTSYNC_DATA_DIR` selects a LocalMachine blob in an un-ACL'd dir | **Accepted → D3:** override ⇒ keyring; machine store only under `is_machine_scope()`; test seams |
| S3/S9 elevated writes + handshake in a dir the principal can write | **Accepted → D0/D2:** `handshake_dir()` pinned per-user; principal RX at root, M on `runs/` only |
| S4 "useless off-box" is false under backups | **Accepted → D3 posture rewritten** |
| S5 full-tree Modify hands the service account the admin's trust state | **Accepted → D2 DACL split** |
| S6 computer accounts pass the gMSA shape (`THISHOST$` = SYSTEM) | **Accepted → D1 validator refuses by name** |
| S7/R1/Y5/P2 silent fall-through → two-profile split-brain | **Accepted → D0:** never fall through; per-user files renamed post-provision; scope shown durably |
| S-cut `mappings/`,`known_hosts` out of machine scope | **Rejected:** self-service overlays must reach the nightly; RX answers the concern |
| R2 per-call syscalls; answer can change mid-run | **Accepted → D0 memo** |
| R3 copytree of a live WAL db | **Accepted → D4 sqlite backup API + integrity check** |
| R4 `/grant:r` before register destroys access on re-provision | **Discharged by owner A10** (Remove prunes, Schedule grants — no in-op switch) |
| R6/P4 configured-but-unreadable secret provisions a nightly that never delivers | **Accepted → D4 pre-UAC gate; `has_secret` in `sftp_is_configured`** |
| R7/P9 principal's folder access unverifiable | **Adapted:** refuse profile/mapped-drive folders; docs say the app cannot check; no probe task |
| R8 unkeyed blob re-pairs an old password to a new account | **Accepted → D3 identity-bound blob** |
| R9/Y2 S0 smoke can't prove the secret without dialling production | **Superseded by owner:** manual domain walk + `--diagnose` |
| R10 registration confirmed by existence only | **Adapted:** read-back displayed; mismatch = WARNING; verdict → ROADMAP |
| Y1 D1 engine is gMSA-only | **Accepted → S-3 after S-2** (and 0046's own deferral condition is now met) |
| Y3/P1/P6 four-way chooser in the wizard; (d) in general build | **Accepted → D6:** wizard unchanged; Settings-only disclosure; caption at point of choice (owner: no branch) |
| Y4 15-char + mandatory-domain rules unverifiable | **Accepted → D1** |
| Y6/P10 shared rotating log; session vs nightly log | **Accepted → per-writer names under `runs/`; sink re-pointed** |
| Y7 discovery vs recorded path | **Decided: HKLM (D0)** |
| Y8 four-valued scope enum; generalised migration helper | **Accepted → boolean; own migration function** |
| Y9 drift detection unrequested | **Accepted → display only** |
| Y10 docs-only final slice | **Accepted → docs ride each slice** |
| P3 delivery line has no true form in the wizard | **Accepted → four forms from read-back** |
| P5 stale blob after a failed re-provision | **Accepted → verify-or-destroy** |
| P7 consequence copy omits one-way + other admins | **Accepted → three sentences + explicit confirm** |
| P8 gMSA failure = generic "try again" | **Accepted → kind-keyed branch + checklist + code on screen** |
| S/R/P-cut refuse (a) on a machine-scoped install | **Rejected:** allowed; honest once scope is visible; ACE pruned at Remove |
| P-cut Azure AD DS as a "verified" basis | **Accepted:** the claim comes only from a district |

## Spec — S-1a (foundation, inert)  _(Stage 4, 2026-09-17; revised after the 3-lens spec review)_

Implements the S-1a row of `## Slices` against v3.21.0 (`8f5761e`). Every seam was re-verified
in the working tree on 2026-09-17; line numbers are anchors, not contracts.

Reviewed by security + reliability-resilience (opus) and YAGNI (sonnet) in design mode.
Both quality lenses returned CHANGES_REQUIRED; YAGNI returned OVER-BUILT. Dispositions are in
`### S-1a.12`. **Two amendments depart from the letter of the approved plan** — both named there.
The orchestrator then re-judged the accepted set and trimmed three acceptances (S-1a.12, "trims").

**Owner decision 2026-09-17: the slice lands as two PRs.** **S-1a-i** (resolution, the switch,
the trust predicate, the CI leg) is complete and inert on its own; **S-1a-ii** (the secret store
and its consumers) depends only on (i). Each PR carries its own tests, tree lines and gates.

### S-1a.0 — The governing invariant

**With the switch off, this slice changes no behaviour.** `is_machine_scope()` is `False` on
every install in the field — nothing outside an elevated `provision` op (which does not exist
until S-1b) can write `HKLM\SOFTWARE\DistrictSync` — so every new branch is dead in production,
the SD74 snapshot is byte-identical, and the only existing tests that move are the four named in
`S-1a.9`, each with a stated reason. Anything else that changes per-user behaviour is a defect.

---

## S-1a-i — resolution, the switch, and the trust predicate

### S-1a-i.1 — `src/utils/dpapi.py` (new) — the shared ctypes helper

- `dpapi_call(func_name: str, data: bytes, entropy: bytes, *, flags: int) -> bytes` — the
  `CryptProtectData`/`CryptUnprotectData` body lifted from `elevation._dpapi`
  (`elevation.py:132-172`): `WinDLL("crypt32", use_last_error=True)`, explicit
  `.argtypes`/`.restype`, `_DataBlob`, `LocalFree` in a `finally`,
  `raise OSError(f"{func_name} failed (error {get_last_error()}).")`. `flags` is **required and
  undefaulted** — the protection scope is the one thing a caller may choose here, and a defaulted
  scope is the permissive safety default `CLAUDE.md` bans.
- Constants live here: `CRYPTPROTECT_UI_FORBIDDEN = 0x1`, `CRYPTPROTECT_LOCAL_MACHINE = 0x4`.
- **`elevation._dpapi` keeps its name, signature and behaviour** and delegates with
  `flags=CRYPTPROTECT_UI_FORBIDDEN` — **not** `flags=0`. Today's call passes that flag
  (`elevation.py:162`, constant at `:87`) and it is load-bearing: without it DPAPI may raise a
  modal prompt inside the `SW_HIDE` elevated child or the non-interactive nightly, converting a
  clean `OSError` into a bounded-wait hang. `tests/test_scheduler_elevation.py:59` calls
  `_dpapi` directly and must not move. `elevation` stays CurrentUser-only; the 2026-06-25 / D5
  decision is not reopened.
- **Test:** a spy asserting the exact `dwFlags` word reaching the API on **both** callers —
  `0x1` from elevation, `0x5` from the machine store. A round trip alone passes with the wrong
  flags, so the round-trip test is not sufficient on its own.

### S-1a-i.2 — `src/utils/accounts.py` (new) — who is running

- `process_account() -> str` — `f"{USERDOMAIN}\\{USERNAME}"` when both env vars are non-empty,
  else `getpass.getuser()`, else `"unknown"`. Never raises.
- `sanitise_account_for_filename(value: str) -> str` — lowercase; `\` and `/` → `_`; anything
  outside `[a-z0-9._-]` → `_`; collapse repeats; truncate to 32; `"unknown"` when empty. Pure and
  total — it names a file, so it may never return `""` or a path separator.
- **`src/scheduler/windows.py` is not touched by this slice.** The earlier draft had
  `current_run_as_user()` delegate here; YAGNI is right that this is churn on a security-adjacent
  module for no functional gain, on the exact file S-3 will rework. The near-duplicate stands.
- **Why a module** (YAGNI proposed folding these into `paths.py`): three consumers in three
  layers — `paths` (log name), `etl/pipeline` and `ui_flet/screens/convert` (the run record) —
  and `src/utils/identity.py` is the standing precedent for exactly this shape (a small module of
  pure, counted primitives). `pipeline.py` importing `paths.process_account` to stamp a run
  record would be worse coupling than the module.

### S-1a-i.3 — `src/utils/paths.py` — ladder, switch, pin, trust

- **Extract, do not rewrite.** Today's `user_data_dir()` body (`:204-221`) becomes
  `_user_scope_data_dir() -> Path`, line for line: override → platform → legacy → create. Every
  documented behaviour survives (blank override = unset, relative = `ValueError`, unusable =
  `RuntimeError`).
- `machine_data_dir() -> Path` — `Path(platformdirs.site_data_dir(_APP_NAME, appauthor=False))`.
  Pure; creates nothing.
- **`MachineScopeRefused(RuntimeError)`** with a bounded `reason` from a `StrEnum`:
  `SWITCH_UNREADABLE · MISSING · NOT_A_DIRECTORY · REPARSE · FOREIGN_OWNER · INHERITED_ACL ·
  INACCESSIBLE` (S-1b adds `OPEN_ACE` when it adds the ACE walk). A single untyped `RuntimeError` would force S-1b's auto-grant screen
  (D5: "switch on ∧ access denied") to string-match `str(exc)` — the fragility `setup_errors`
  exists to avoid. `str(exc)` names the path and the reason; `--diagnose` (S-1b) prints the
  reason.
- `_machine_switch_on() -> bool` — `sys.platform == "win32"` and `HKLM\SOFTWARE\DistrictSync`
  has `MachineScope` as **`REG_DWORD` == 1**. `winreg` is imported inside the win32 branch.
  Opened with `MACHINE_SCOPE_KEY_ACCESS = winreg.KEY_READ | winreg.KEY_WOW64_64KEY`, exported as
  a module constant **S-1b's writer must import** — a writer in the redirected 32-bit view would
  commit `WOW6432Node\DistrictSync`, report success, and leave the app per-user with the config
  and secret already copied.
  - `FileNotFoundError` (key or value absent) → `False`, silent. This is the normal state.
  - Value present but not `REG_DWORD`, or not `1` → `False` + one WARNING naming the key.
  - **Any other `OSError` → `MachineScopeRefused(SWITCH_UNREADABLE)`.** Not `False`. "Any
    exception → off" is the forbidden fall-through moved one step earlier: on a provisioned
    install it silently selects the principal's blank profile and reproduces the exit-3 nightly
    the plan's `## Problem` describes. On a per-user install this state is close to impossible
    (`HKLM\SOFTWARE` grants Users read; a missing key is deterministic `FileNotFoundError`), so
    the risk of refusing a healthy install is far smaller than the risk of downgrading a shared
    one. *(The two reviews disagreed here — security proposed `False` + WARNING. Recorded in
    S-1a.12.)*
- `_assert_machine_dir_trusted(path) -> None` — raises `MachineScopeRefused` unless **all** hold:
  1. exists and is a directory (`MISSING` / `NOT_A_DIRECTORY`);
  2. not a reparse point — `os.lstat(path).st_reparse_tag == 0`, stdlib, no ctypes (`REPARSE`);
  3. **owner SID** ∈ {`S-1-5-32-544`, `S-1-5-18`} — ctypes
     `advapi32.GetNamedSecurityInfoW(OWNER_SECURITY_INFORMATION)` + `ConvertSidToStringSidW`,
     `LocalFree` in a `finally`, following the `elevation.py` convention
     (`WinDLL(..., use_last_error=True)`, explicit argtypes). **Compare SID strings, never
     account names** — `BUILTIN\Administrators` is localised (`FOREIGN_OWNER`);
  4. **inheritance is disabled** — `SE_DACL_PROTECTED (0x1000)` read from the security
     descriptor's control word (`GetSecurityDescriptorControl`) (`INHERITED_ACL`).
  - Any failure to read owner or control word → `INACCESSIBLE`. **Fails closed.**
  - **Why 4 is in this slice:** D3 states the machine store's confidentiality boundary *is* the
    NTFS DACL, and S-1a-ii ships the code that writes the credential. An admin who hand-sets the
    registry value and hand-creates the directory passes owner-and-reparse with
    `C:\ProgramData`'s inherited `Users:(OI)(CI)(RX)` intact — and a LocalMachine blob under a
    non-secret entropy is then world-decryptable. The control bit catches that *mistake* in a
    handful of lines.
  - **Why the open-group ACE walk (Everyone / Authenticated Users / Users / Interactive) is
    S-1b's, not this slice's:** an explicit grant to `Users` *after* stripping inheritance is a
    deliberate act — tampering-class, which the plan's threat model excludes — and S-1b is the
    slice that writes the DACL and reads it back through `icacls`. Hand-rolled ACE enumeration
    in ctypes inside `paths.py` would be the riskiest code in the slice for the least likely
    case. S-1b adds the walk and the `OPEN_ACE` reason together.
- **The pin.** Module-level `_PIN: tuple[Path, bool] | None`. `user_data_dir()` returns `_PIN[0]`
  when set, else resolves once and stores `(path, machine_scope)`: `DISTRICTSYNC_DATA_DIR` set →
  `(_user_scope_data_dir(), False)` — **the override is never machine scope**; else
  `_machine_switch_on()` → `_assert_machine_dir_trusted(d)` → `(d, True)`; else
  `(_user_scope_data_dir(), False)`. Path and scope are decided in one breath so they cannot
  disagree.
- `is_machine_scope() -> bool` — resolves the pin if needed, returns `_PIN[1]`. The ONE predicate
  consumers branch on. `pin_data_dir() -> Path` forces resolution now and logs
  `"DistrictSync data dir: <path> (machine scope: yes/no)"`. `reset_data_dir_pin() -> None`
  clears it (tests; S-1b's post-provision reload).
- `handshake_dir() -> Path` — **always** the per-user location, never the machine dir, and
  **non-creating**: it resolves without `mkdir`. The elevation request/result blobs are a
  per-user CurrentUser-DPAPI artefact that must not follow the profile into a directory another
  principal can write. `elevation.py:251` and `:259` (the two writers) call it and mkdir
  explicitly; `elevation.py:291` (`sweep_orphans`) calls it and **returns 0 when the directory
  does not exist**. Non-creating matters: `sweep_orphans` runs unconditionally at both entry
  points (`main.py:459`, `launcher.py:195`), so a creating resolver would have every nightly as
  the service principal materialise an empty second profile — the very thing D0 forbids.
- `user_log_file()` → `user_data_dir() / "runs" / f"etl_tool-{sanitise_account_for_filename(process_account())}.log"`
  on machine scope (two writers never contend on one rotating handler), else **unchanged**.
  `user_history_db()` → `runs/history.db` on machine scope, else unchanged. `user_mappings_dir()`
  needs no change. `runs/` is created by its writers, as today.
- `migrate_legacy_data_dir()` is **untouched** — it resolves `_platform_data_dir()`/
  `_legacy_data_dir()` directly and never sees the machine dir.

### S-1a-i.4 — Entry points: the refusal must land somewhere

A `MachineScopeRefused` raised at resolve time currently has no catcher on either path, and both
produce a false report.

- **`main._cli`** — `cli()` catches only `SystemExit` (`main.py:403-406`) and `pin_data_dir()`
  would sit ahead of `_configure_cli_logging()` (`:447`). Fix: call `pin_data_dir()` inside a
  `try`; on `MachineScopeRefused`, configure logging against `handshake_dir()` (always
  resolvable), emit **one** anchored ERROR naming the path and the `reason`, and return the
  documented **exit 1**. Never a bare traceback to a stderr Task Scheduler discards.
- **`launcher.main`** — `resolve_log_path()` → `user_log_file()` → re-raises, so
  `_write_traceback` falls back to a bare relative `etl_tool.log` (`launcher.py:66-69`) inside
  the `sys._MEIPASS` cwd that is deleted on exit, while `format_user_error` tells the admin
  *"Your scheduled nightly sync is not affected — it runs separately"* (`:81`) — false for
  precisely this failure. Fix: resolve the crash-log path against `handshake_dir()` when the pin
  is unavailable, and give this branch its own copy naming the shared folder and the reason.
  One filled primary, `ErrorCard` floor, per the design system.

### S-1a-i.5 — CI: a `windows-latest` pytest leg

`.github/workflows/ci.yml` gains a second job `test-windows`: `runs-on: windows-latest`,
`actions/setup-python` 3.13, the same install as the ubuntu job (`pywin32` already rides
`requirements.txt:28`), `python -m pytest tests/ -q`. **No coverage gate on this leg** —
coverage stays the ubuntu job's single gate; a second `--cov-fail-under` over a different skip
set is a second, differently-calibrated gate. The ubuntu job is unchanged so the existing
required check keeps its name. Lint/type/security/config steps are not duplicated.

- The runner's token is **not assumed and not depended on**: no test may read the runner's real
  elevation state — every `is_elevated()`-dependent branch is driven by monkeypatch on both
  legs, so a change in how GitHub launches the job can never flip a test's path silently.
- The raw syscall wrappers (`dpapi_call`, the `GetNamedSecurityInfoW` reader, `_machine_switch_on`'s
  `winreg` call) carry `# pragma: no cover` **on the syscall line only**; all logic around them is
  reached through monkeypatched seams on every OS, so the ubuntu coverage gate does not fall.
- **Risk the implementer surfaces rather than absorbs:** no `WINDOWS_ONLY`-marked test has ever
  run in CI. If the leg goes red on something S-1a did not touch, **stop and report** — fixing
  pre-existing Windows-only failures is a separate decision.

---

## S-1a-ii — the secret store and its consumers

### S-1a-ii.1 — `src/sftp/secret_store.py` (new)

- `SecretStore` protocol: `store_password(host, username, password) -> None`,
  `get_password(host, username) -> str | None`, `has_secret(host, username) -> bool`.
- `UserSecretStore` — today's keyring, byte-identical:
  `keyring.{set,get}_password(KEYRING_SERVICE, username)`. **`host` is accepted and ignored**:
  the shipped key has always been username-only and re-keying it would silently invalidate 20
  live installs' stored passwords. Stated so the asymmetry is a decision, not an oversight.
- `MachineSecretStore` — `machine_data_dir()/"sftp_secret.bin"`.
  - **Identity is bound in the DPAPI entropy, not merely compared after decrypt:**
    `entropy = b"DistrictSync/sftp-secret/v1|" + host + b"|" + username` — the **exact**
    stripped strings, not case-folded, matching the keyring's exact-key semantics. A mismatched
    identity fails the unprotect itself, so `has_secret` never materialises a password to answer
    a boolean, and a blob cannot be re-paired to a different account. The payload also carries
    `{host, username, password}` and is re-checked after decrypt (belt-and-braces). Entropy
    distinct from elevation's `DistrictSync/elevation/v1`.
  - `protect_machine_blob`/`unprotect_machine_blob` hardcode
    `flags=CRYPTPROTECT_LOCAL_MACHINE | CRYPTPROTECT_UI_FORBIDDEN` (`0x5`).
  - **Verify BEFORE promote** — write `sftp_secret.bin.<token_hex>.tmp` → read the **tmp** back →
    unprotect → compare all three fields → only then `os.replace` onto the live name. The live
    blob is therefore only ever replaced by a proven-good one; **a failed write never destroys a
    working secret**, and the failure arm only ever unlinks a tmp that was never live. A unique
    tmp name (house pattern, `elevation.py:251`) means two writers cannot collide; a stale
    `sftp_secret.bin.*.tmp` is swept at entry, because it holds a sealed password and no existing
    sweep reaches it.
  - `has_secret` is **total by contract** — any failure (missing file, `OSError`, DPAPI error,
    identity mismatch) returns `False`, logged once at WARNING, never the value. It feeds a pure
    predicate called from UI paint paths, which may not start raising. **No memo:** one
    `CryptUnprotectData` with `UI_FORBIDDEN` is sub-millisecond, and a cache inside a secret
    store is a bug surface, not a feature. Revisit only if S-2's walk shows a laggy repaint.
  - No password value ever reaches a log line, an exception message, a result dict or a `repr`.
- `select_store() -> SecretStore` — `MachineSecretStore()` iff `paths.is_machine_scope()`, else
  `UserSecretStore()`. **Never both, never a fallback chain.** Under `DISTRICTSYNC_DATA_DIR` the
  answer is always the keyring, by construction of the pin.

### S-1a-ii.2 — Routing the existing callers

- `SFTPUploader.store_password` / `_get_password` / `get_stored_password` keep their **public
  signatures** (4 src call sites, 11 test files) and delegate to `select_store()` with
  `self.host`/`self.username`. Nothing above the uploader learns a store exists.
- `AppConfig.sftp_is_configured()` (`app_config.py:805-811`) gains **one** conjunct, **only on
  machine scope**: `select_store().has_secret(host, username)`. Per-user it is byte-identical —
  adding a keyring read there would change 20 installs' behaviour. Lazy import inside the method
  (the `ALLOWED_SFTP_HOSTS` import below it is the precedent). The method stays total.
- Zip staging (`uploader.py:575`): `upload_csvs` gains `staging_dir: Path | None` (keyword,
  default `None` = today's `%TEMP%`, byte-identical). **Only the scheduled nightly on a
  machine-scoped install** passes `machine_data_dir()/"runs"/"tmp"` — `pipeline._sftp_upload`
  decides from `is_machine_scope()` **and** its already-resolved source (`_resolve_source`,
  `pipeline.py:668`) being `scheduled`. The profile-load race that motivates `runs/tmp` only
  bites the nightly principal; the admin's manual Convert and a hand-run CLI have a loaded
  profile and keep `%TEMP%`. That also closes the window security named — `runs/` grants the
  principal Modify by design, and the staged zip is student PII leaving the building, so the
  admin's zip must never sit where the service account can swap it — without an `icacls` call
  per upload or an `sftp → scheduler` import.
- `main._sftp_configure`/`_sftp_test` inherit the routing through the uploader (`main.py:264` is
  the only direct call). `_sftp_show` touches no secret.
- `pin_data_dir()` is called at both entry points per S-1a-i.4.

### S-1a-ii.3 — `run_as` on the run record  *(amended — no schema change)*

`read_run_records` is `SELECT record FROM runs` and returns only the parsed JSON blob; the
promoted columns (`source`, `error_category`) are *also* carried inside that blob, and
`convert.py:546` already sets `record["delivery_only"] = True` with the comment *"rides free in
the store's JSON blob (additive, no schema change)"*. So:

- `run_as` is a **key in the record dict**, carrying `accounts.process_account()`, set in
  **both** record builders — the pipeline's (so the `__DISTRICTSYNC_RUN__` log line and the store
  row carry the same dict) and `convert._record_manual_run` (`convert.py:~540`). Not stamped
  inside `write_run_record`: the store must not mutate what it is handed, and the log line is
  written from the same dict before the store sees it.
- **No `SCHEMA_VERSION` bump, no `ALTER TABLE`, no `write_run_record` kwarg.** The plan's S-1a
  row says "store `run_as` column (additive, `user_version` 2)"; the column would be invisible to
  the only reader, and the migration has a measured brick state — `sqlite3` runs DDL in
  autocommit, so the `ALTER` and the `PRAGMA user_version=2` are two commits: a crash or a race
  between them leaves `user_version==1` **with** the column, the next run re-ALTERs, raises
  `duplicate column name`, and `_is_corrupt()` returns `False` for it, so no quarantine-recreate
  fires and `write_run_record` returns `False` every night thereafter while the nightly keeps
  reporting success. The record-dict key delivers the same user-visible outcome (S-2 shows which
  account a night ran as) and deletes that failure mode outright. **Amendment 1 of 2.**
- `run_as` is an OS account name — never a student identifier. It stays in the local store and
  the local log, exactly as the account name already does.

---

### S-1a.9 — Tests (both halves)

New `tests/test_secret_store.py`; additions to `tests/test_paths.py`,
`tests/test_history_store.py`, `tests/test_logger.py`, `tests/test_scheduler_elevation.py`,
`tests/conftest.py`.

- **Ladder, both directions.** Switch on ∧ trusted → the machine dir **and**
  `is_machine_scope() is True` (the positive twin). Switch on ∧ each refusal reason → the typed
  `MachineScopeRefused` with that `reason`, **and no fall-through** (assert the per-user dir was
  not returned). Switch off → today's answer exactly. Override ∧ switch on → the override, scope
  `False`. Non-win32 → scope always `False`. Registry: absent key → `False` silent; wrong type →
  `False` + WARNING; `PermissionError` → `SWITCH_UNREADABLE`.
- **Pin.** Resolves once (assert the registry is read once via a call counter);
  `reset_data_dir_pin()` re-resolves; `user_data_dir()` and `is_machine_scope()` always agree.
- **`handshake_dir()`** never returns the machine dir with the switch on; equals `user_data_dir()`
  with it off; does **not** create; `sweep_orphans` returns 0 on an absent directory.
- **DPAPI flags spy** — exact `dwFlags` on both callers (`0x1`, `0x5`), plus the existing real
  round trip, plus a new `WINDOWS_ONLY` real LocalMachine round trip.
- **Select rule truth table** — machine ⇒ machine store only (the in-memory keyring is never
  touched), user/legacy/override ⇒ keyring only (nothing is written under the machine dir).
- **Identity binding** — a blob sealed for `(hostA, userA)` yields `False`/`None` for
  `(hostA, userB)` and `(hostB, userA)`, and the failure is an unprotect failure, not a payload
  comparison.
- **Write atomicity** — a read-back that disagrees leaves the **previous live blob intact** and
  raises; a stranded `.tmp` is swept; two interleaved writers never destroy a committed secret.
- **Non-leak** — the password never appears in `caplog.text`, an exception string, or any `repr`,
  across store / get / verify-failure / sweep paths.
- **`sftp_is_configured`** — machine ∧ no secret → `False`; machine ∧ secret → `True`; per-user
  with no keyring entry → **`True`** (today's behaviour, deliberately unchanged); a raising store
  → `False`, never a propagated exception.
- **`run_as`** — present in the dict returned by `read_run_records` after a pipeline and a manual
  run; absent-key records from older DBs still read; **no** `user_version` change (assert it is
  still 1 on a fresh DB — the positive twin being that the value round-trips anyway).
- **Log/db names** — machine → `runs/etl_tool-<account>.log` and `runs/history.db`; per-user →
  byte-identical to today.
- **AC1 has a mechanical twin** — an AST/grep test asserting no registry **write** API
  (`SetValueEx`, `CreateKey`, `KEY_WRITE`, `KEY_ALL_ACCESS`) appears anywhere under `src/`. It is
  what makes "nothing can turn the switch on" a fact rather than a claim. **S-1b moves it
  deliberately** — add it to the handover's "tests that must move" list.
- **The migration pin gets its positive twin** — switch on ∧ legacy present ⇒ the legacy tree
  still lands in the **per-user** platform dir.

**Existing tests that move, each deliberately** (there are exactly four):
`tests/conftest.py::isolated_user_profile` patches **`_user_scope_data_dir`** (with
`user_data_dir` delegating) instead of `user_data_dir` alone — otherwise the pin resolves through
a function the seam does not cover and the headline truth table is a vacuous green; it also calls
`reset_data_dir_pin()` on entry and teardown, and forces `_machine_switch_on → False` so the
default is OFF for all 6,292 tests. The canary's `_REAL_PROFILE_DIR` (`conftest.py:89`) extends
to the platform dir, which it does not watch today. `TestSweepOrphans::test_deletes_old_keeps_fresh`
and `::test_sweep_ignores_unrelated_files` (`test_scheduler_elevation.py:205-235`) seed into
`user_data_dir()` and move to `handshake_dir()`.

### S-1a.10 — Docs

`docs/claugentic-ARCHITECTURE_TREE.md` — one line each for the three new modules (the implementer
writes the descriptions; the pre-commit gate aborts otherwise). `CLAUDE.md` — **extend the
existing `DISTRICTSYNC_DATA_DIR` paragraph** rather than adding a second one describing the same
ladder: the HKLM switch and its access mask, the never-fall-through rule and the typed refusal,
the select rule, `handshake_dir()` staying per-user and non-creating, and the required `flags`.
**No CHANGELOG entry** — nothing user-visible ships, and an entry would announce a feature nobody
can reach. **No new DECISIONS entry** unless an amendment below is adopted, in which case it is
recorded with its evidence.

### S-1a.11 — Out of scope (named, so it is not half-done)

Provisioning, the HKLM **write**, DACL **application**, migration, the auto-grant screen,
`--diagnose`, `prune_principal` (S-1b) · every UI string, gate and predicate, and Run History's
display of `run_as` (S-2) · `PrincipalKind`, `validate_gmsa_account`, the elevation predicate
(S-3) · the gMSA disclosure (S-4). The DACL apply-then-`icacls`-read-back test belongs to S-1b,
the slice that writes a DACL.

**Known verification hole, named for S-1b:** the exe smokes and the fresh-profile QA walk set
`DISTRICTSYNC_DATA_DIR` by mandate, and the override is never machine scope — so no automated
path can exercise machine scope end to end. The only proof is the owner's manual walk. S-1b must
either add a test-only seam in the frozen build or state the gap explicitly.

### S-1a.12 — Review dispositions

**Accepted from security (10/10, two in part):** UI_FORBIDDEN flag preserved · DACL
inheritance check added to the trust predicate (**in part** — the open-group ACE walk moves to
S-1b, see S-1a-i.3) · refusal lands on both entry points · unique tmp + never destroy a live
blob + tmp sweep · typed refusal reason · zip staging exposure closed (**in part** — by scoping
`runs/tmp` to the scheduled nightly rather than an `icacls` per upload, see S-1a-ii.2) ·
non-creating `handshake_dir`/sweep · identity bound in the entropy (exact strings) · REG_DWORD +
exported access mask · conftest seam corrected.
**Accepted from resilience (10/10, one in part):** schema migration deleted in favour of the
record key (kills its own finding 1) · verify-before-promote · registry read not permissive ·
both entry points · launcher copy · conftest/canary seams · `has_secret` total (**in part** —
no memo) · AC1's mechanical twin · migration positive twin · CI leg caveats (tests never depend
on the runner's elevation state).
**Trims (orchestrator re-judgment, 2026-09-17):** the three "in part" items above. Each keeps
the finding's substance and drops the implementation that would have been the riskiest code in
the slice for the least likely case.
**Accepted from YAGNI (2/6):** `windows.current_run_as_user()` delegation dropped · CLAUDE.md
merged into the existing paragraph. **Declined (2):** `accounts.py` and `dpapi.py` stay separate
modules — folding account resolution into `paths.py` inverts the dependency for `pipeline`/
`convert`, and exporting a flags-taking DPAPI primitive from `scheduler/elevation.py` would make
`src/sftp/` import from `src/scheduler/`, a worse inversion than a shared `utils` module;
`src/utils/identity.py` is the standing precedent for the shape. Its two KEEP-BUT-JUSTIFY items
(the `SecretStore` protocol, and `run_as` + the CI leg belonging to S-1a) are upheld.
**One disagreement, adjudicated:** on a non-`FileNotFoundError` registry failure security
proposed `False` + WARNING, resilience proposed treating it as unknown. Resolved in favour of
refusing, because the plan's own hard constraint is "never fall through from a set switch to a
per-user profile" and an unreadable switch cannot prove the switch is unset.

**Amendments to the approved plan — owner decision required:**
1. `run_as` ships as a record-dict key, **not** a `runs` column with `user_version` 2
   (S-1a-ii.3). Same outcome for S-2, one measured brick state deleted.
2. "Verify-or-destroy" becomes **verify-before-promote**: a blob that cannot be proven good never
   becomes live, and the live blob is never destroyed by a failed write (S-1a-ii.1). This serves
   the plan's stated goal — "a stale secret is unrepresentable" — without the arm that turns a
   transient AV file-lock into "delivery is now unconfigured" on an install whose principal
   cannot be logged into.

### S-1a.13 — Acceptance criteria

1. `is_machine_scope()` is `False` on every install in the field, **proven** by the no-registry-
   write test, not asserted.
2. The full suite passes with exactly the four deliberate test moves named in S-1a.9 and no
   others; the SD74 snapshot is byte-identical; coverage stays ≥ 80 %.
3. Every new branch is tested on both sides; every refusal has a positive twin; no "X did not
   happen" assertion ships without one.
4. No secret value reaches a log, an exception, a result dict or a `repr`; no failed write
   destroys a working secret.
5. All local gates green (pytest+cov · ruff check · ruff format --check · mypy non-UI · bandit
   `-c pyproject.toml` · tree-check · `check_no_emails.py` · validate-config inline), and **CI's
   own `test pass` line read and quoted on both legs** before the slice is called landed.
