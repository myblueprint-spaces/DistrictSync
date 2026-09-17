# 0049 — Machine-scoped install + gMSA principal (plan 0046's parked half, unparked)

- **Status:** APPROVED 2026-09-17 (owner, in session) as the v2 design; **rebased here onto v3.21.0** (`8f5761e`) after the rebase found plan 0046 B/C/D and plan 0047 A1/A2 already shipped — see *Rebase notes*. Not started.
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
| S-4 | gMSA disclosure (Settings only) + untested caption + MSA error branch + GPO-branch pointer | **SD60** (next normal release) | `troubleshooting.md` + parity test; CHANGELOG "available, untested" |

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
