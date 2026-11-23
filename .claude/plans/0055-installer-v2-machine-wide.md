# 0055 — Installer v2: one machine-wide Windows installer that replaces the one-file exe

- **Status:** Approved 2026-11-23 (owner, relayed). Reviewed twice (refuting) and once for scope. Building in slice order from G0.
- **Roadmap item:** epic DSYNC-4 "Distribution & trust" (new tickets listed under Decomposition).
- **References:** plan 0054 (R1, S0 row 13, "Gaps to full coverage" 1-7) · plan 0049 + `docs/developer/machine-scope.md` · `docs/developer/scheduler.md` · `docs/developer/updates.md` · `docs/partner/headless-sftp-setup.md` · `docs/partner/managed-service-accounts.md` · `docs/developer/sd74-hosted-server.md` · `docs/DECISIONS.md` 2026-11-17, 2026-11-19 (DSYNC-87, the R15 exception).

**Evidence labels.** **[S0b]** proven by running in this plan's spike (2026-11-23, below). **[S0]** plan 0054's spike. **[V]** read in this repo on 2026-11-23. **[I]** inference. **[N]** not verified; a slice must settle it.

## Problem

The owner, 2026-11-23: "we need to migrate all functionality from the onefile exe over to this installer ... more robust and verbose and better than the one file and tested in every way so that we can discard the onefile". Also: districts do not set this up as a named user and do not want people's names in logs; and districts running it on internal servers may see a pushed update as a security risk, so they must be able to enable or disable it.

Today's installer (v3.30.0, plan 0054) cannot replace the one-file exe:

1. It is **per-user only** (`%LOCALAPPDATA%\myBlueprint.DistrictSync`) [V].
2. A **service-account or gMSA nightly cannot run it** (0054 R1): the program sits in one person's profile, which other accounts cannot read [I, 0054].
3. **Nothing stops Setup → Schedule registering a foreign principal against a per-user installed copy** [V]: no reader of the install kind exists in `src/scheduler/` or `screens/setup.py`; `runs_from_user_writable_install` (`src/scheduler/task_com.py:309-315`) only decides the run level.
4. **Machine scope switches updates off** (`src/update/check.py:118-156`, guard 3) [V], and nobody is defined to apply an update to a shared install.
5. **District IT has no update control**: `AppConfig.update_enabled` (`src/config/app_config.py:212`) has no screen and no Group Policy reader [V].
6. **No migration** from the one-file exe or from the per-user installer, and no command-line way to register the nightly (`src/main.py:603-634` has none; docs fall back to raw `schtasks`) [V].
7. **Upgrade over an existing install has never run on a real machine**, and uninstall/reinstall guarantees are unproven [V `sd74-hosted-server.md`].
8. **Install, upgrade and uninstall are silent**; `--diagnose` says little about the install [V].

Two code facts found while planning make machine-wide unsafe today, so they are slices, not footnotes:

- `install_root()` (`src/update/startup.py:43`) and `runs_from_user_writable_install` test only the folder **layout** (`Update.exe` two levels up). A Program Files install has the same layout, so it would be classed "user-writable" [V].
- `run_entry()` (`src/main.py:32-37`, `src/update/entry.py:73-91`) resolves and creates the profile **before** Velopack's `--veloapp-*` hooks get control. The MSI runs those hooks from Windows Installer, and a Group Policy or Intune install runs as SYSTEM, so a hook would create `C:\Windows\System32\config\systemprofile\AppData\Local\DistrictSync` [V code, I runtime].

## Owner decisions (grill, 2026-11-23)

| # | Question | Answer |
|---|---|---|
| G1 | Update default on a machine-wide install | **Off; district IT opts in.** Settings toggle, plus an HKLM Group Policy key that overrides both. |
| G2 | Who applies an update to a machine-wide install | **One SYSTEM scheduled task, "DistrictSync Updater"**, present only while updates are on. Signed manifest + Authenticode, never during a run, right after the nightly. |
| G3 | Install shape | **Machine-wide only.** Admin installs once to Program Files; the profile is `C:\ProgramData\DistrictSync`. Per-user v3.30.x installs are migrated. |
| G4 | One-file end of life | **Keep publishing it until every known district is confirmed migrated**, then drop it. |
| G5 | Where gMSA / Server Core / service-account proofs run | **Azure test VMs** (domain controller, member server, Server Core) in `customer-experience-ca`, throwaway. |

Earlier decisions still apply: pack id `myBlueprint.DistrictSync`; feed on `integration.crm.myblueprint.org`; ES256 manifest in Key Vault; the shared `myb_desktop_updater` package; Velopack 1.2.161 (DECISIONS 2026-11-17).

## S0b spike: can Velopack do machine-wide? (2026-11-23, owner's Windows 11 Home PC, two owner-approved UAC prompts)

What was run: a small one-folder PyInstaller app (the `velopack` 1.2.161 Python package, `App().run()` with apply-at-start off, exit code 7 to prove pass-through), packed unsigned with `vpk` 1.2.161 `--msi --instLocation PerMachine --runtime win-x64` as versions 1.0.0 and 1.0.1 under pack id `myBlueprint.VSpike`. It was installed, updated, upgraded and uninstalled, then everything was removed. The scripts and logs are in the session scratch folder; the findings below are recorded in the side-session notes too. **The real DistrictSync build was not used** (the remaining questions are slice L1).

| # | Question | Result [S0b] |
|---|---|---|
| 1 | Does `vpk` 1.2.161 build a machine-wide installer? | **Yes, as an MSI.** `--msi` plus `--instLocation PerUser\|PerMachine\|Either`. It is a real WiX MSI: `ALLUSERS=1`, the app's files in the `File` table, 13.7 MB for an 11 MB app. The default silent folder is `[ProgramFiles64Folder][packId]` (`C:\Program Files\myBlueprint.DistrictSync`); the public property `VELOPACK_INSTALLDIR` chooses another. The UpgradeCode is fixed per pack id, so a newer MSI is a major upgrade. Its dialogs are Velopack's fixed set (welcome, folder); **we cannot add a checkbox to the MSI** [S0b table read]. |
| 2 | Silent machine-wide install | `msiexec /i … /qn VELOPACK_INSTALLDIR="C:\Program Files\myBlueprint\VSpike"`: exit 0 in 2 s. Layout `Update.exe`, stub exe, `current\`, `packages\`, `.msi-installed`. Folder ACL is Program Files' own (Users read/execute; Administrators, SYSTEM, TrustedInstaller full). Apps & features shows one visible entry `MSI:myBlueprint.VSpike` (uninstall string `msiexec /x {ProductCode}`); the MSI product itself is hidden. |
| 3 | A standard (non-admin) user runs it | Runs; exit code passed through; correct version. **But `App().run()` prints `Failed to open directory for writing "…\.velopack_dir_test": Access is denied. (os error 5)` to the console on every unelevated start, twice.** Every service-account nightly and every IT command line would show it. |
| 4 | A non-admin process asks Velopack to apply an update | Velopack copies `Update.exe` and downloads the package into **that user's** `%LOCALAPPDATA%\<packId>\`, then **re-launches `Update.exe` as administrator (a UAC prompt)**. Declined: "The operation was canceled by the user", version unchanged (it fails safe). A nightly has nobody to answer the prompt, and elevating a copy of `Update.exe` that sits in a user-writable folder is a hazard. **So on a machine-wide install, no non-SYSTEM process may ever call Velopack's apply.** |
| 5 | A SYSTEM scheduled task applies an update | **Works with no prompt.** 1.0.0 → 1.0.1 applied after exit. The folder ACL was unchanged, and Velopack updated the visible Apps & features version to 1.0.1 (the hidden MSI product stays 1.0.0). This is the G2 model, proven on the toy app. |
| 6 | `msiexec /x` after Velopack moved the version | Removes the whole install folder, exit 0. A stand-in profile in `C:\ProgramData` was untouched. |
| 7 | Upgrade over an existing install, MSI over MSI | 1.0.0 MSI then 1.0.1 MSI: major upgrade, exit 0, same custom folder. A repair without the folder property stayed in that folder. |
| 8 | `Setup.exe --silent --installto "C:\Program Files\…"` (elevated) | It installs there **but registers itself per-user (HKCU)**. That is not a machine-wide install, so **v2 ships the MSI, not `Setup.exe`**. |
| 9 | Install and uninstall hooks | Windows Installer runs `--veloapp-install` / `--veloapp-uninstall` as deferred custom actions impersonating the installing account; the folder delete runs as SYSTEM. The uninstall hook is passed the MSI's version, not the version Velopack moved it to (harmless, noted). |

**Verdict.** Velopack can do it: install machine-wide with its MSI and apply updates from a SYSTEM task. No alternative installer technology is needed. The two things it does wrong for us (the console line, and a non-admin apply that prompts) are avoided by our code, not by Velopack (slices M1, U2).

**Not proven yet (slice L1, on the Azure lab):** a gMSA and a local service account running the real build from Program Files; Server Core; the Flet window from Program Files; a SYSTEM apply while the nightly is running; the MSI signed through `vpk --azureTrustedSignFile`; Group Policy / Intune deployment; re-pointing a password-principal task without its password; whether Velopack's process check sees processes in other sessions.

## Goals / Non-goals

**Goals**
1. One signed `DistrictSync.msi` that an administrator installs once, by double-click, `msiexec /qn`, Group Policy, Intune or SCCM, to `C:\Program Files\myBlueprint.DistrictSync\`, and that works for every principal the one-file exe supports: the signed-in account, a stored-password service account, a gMSA, on desktop Windows, Server with Desktop Experience and Server Core.
2. Everything the one-file exe does, the installed copy does from the same place: window, CLI and exit codes, `--sftp-*`, `--diagnose`, Setup's schedule with every principal, elevation, the schedule check, machine scope.
3. District IT controls updates: off by default, a Settings toggle, an HKLM policy that wins and that the app cannot override, and Group Policy templates (ADMX/ADML) for it. When updates are on, one SYSTEM task applies them: signed only, never during a run, rolled back if the new version cannot start. When updates are off, the app says plainly how far behind it is (O1).
4. Migration from the one-file exe and from the v3.30 per-user installer, in the window or headless, carrying settings, history, the SFTP secret and the nightly.
5. Uninstall and reinstall never remove the profile, the secret or the nightly task.
6. Plain, verbose install, upgrade, update and uninstall records that district IT can read, and `--diagnose` lines that say what is installed, where, what updates it and when it last tried.
7. No person's name in any log line, log file name or file the nightly writes (owner, 2026-11-23).
8. Every row of the test matrix below proven on a real machine before the one-file exe is retired.

**Non-goals**
- Any converter (ETL) change. **This plan does not touch the converter**; the SD74 snapshot still gates every slice, and a slice that turns out to touch it gets a before/after run on every affected district drop.
- macOS and Linux (unchanged).
- A per-user install shape (G3). The per-user `Setup.exe` stops being attached once the MSI ships (slice R2); the code that serves per-user copies stays until those copies are migrated.
- Our own MSI authoring (WiX by hand) or a non-Velopack installer. S0b says not needed.
- Turning the feed on. Automatic updates still need plan 0054's S6b and S8 (the Azure provisions). v2 ships with the updater wired and inert until then, exactly as v3.30.0 shipped its check.

## Approach

### 1. The install

- **Vehicle:** `vpk pack … --msi --instLocation PerMachine --packTitle DistrictSync --shortcuts StartMenuRoot` in `release.yml`'s installer job, beside today's per-user `Setup.exe` until R2 retires it. The MSI is Authenticode-signed (whether `vpk --azureTrustedSignFile` signs the MSI itself is [N], settled in slice B1's rehearsal; if not, `signtool` signs it in the same job with the same identity before the verify step).
- **Folder:** Velopack's default, `C:\Program Files\myBlueprint.DistrictSync\` (follows the pack id, so IT never has to pass a folder property; the owner's earlier sketch said `myBlueprint\DistrictSync`, and `VELOPACK_INSTALLDIR` can still choose it). The scheduled task and every doc use `…\current\DistrictSync.exe`; the root stub stays unusable for the CLI [S0 #6].
- **Profile:** always `C:\ProgramData\DistrictSync` (plan 0049 machine scope). A machine-wide install never uses a per-user PROFILE; if machine scope is not provisioned yet, the app says so and offers machine setup (section 2) instead of creating one. **Declared per-user exceptions, unchanged from 0049:** the elevation handshake directory (`handshake_dir()`, per-user and non-creating in every scope by design, `paths.py:760-773`) and the `MOVED.txt` fence written into the admin's old per-user profile by `complete_handover`. 
- **Who can open it (0049's access model, unchanged).** The root DACL holds SYSTEM, Administrators (elevated token only), the setup user and the nightly principal (`provisioning.py:410-449`). A second administrator or a non-admin user who opens the window gets `INACCESSIBLE` and 0049's grant window, which needs one UAC consent by an administrator; over-the-shoulder consent fails closed. v2 keeps that model and documents it in the IT guide; T20 and T23 test it as it is.
- **Install kind, one reader.** New `src/utils/install_kind.py:install_kind() -> InstallKind` (`PORTABLE`, `PER_USER`, `MACHINE`), the ONE place this is decided: `MACHINE` iff the installed flavour, the Velopack layout, the `.msi-installed` marker beside `Update.exe`, AND an ACL check on the install root and `current\`: no ALLOW ace granting any write, append, delete, change-permission or take-ownership right to a SID outside the trusted set {SYSTEM `S-1-5-18`, Administrators `S-1-5-32-544`, TrustedInstaller `S-1-5-80-956008885-…`}; INHERIT_ONLY aces (e.g. CREATOR OWNER on Program Files) are ignored because they apply to children created later, not to this folder; read/execute aces (Users, ALL APPLICATION PACKAGES) are allowed. SID strings, the 0049 way; never the folder's path. A layout that fails is `PER_USER` (fails toward the safer answer). **Custom folders:** an MSI install to `VELOPACK_INSTALLDIR="D:\Apps\…"` inherits `D:\`'s default (Authenticated Users: Modify) and so fails; machine setup offers to apply the Program Files-equivalent protected DACL to that install root (part of its one elevated op), and until it has, `--diagnose` and Setup name the cause ("the install folder is writable by non-administrators"). The test fixtures use the real Program Files DACL and the real `D:\` default, not a hand-made "same ACL". `install_root()`, `runs_from_user_writable_install()`, the update guards, Setup and `--diagnose` all read it. Tests pin a Program Files layout and a `D:\` layout with the same ACL as `MACHINE` and never "user-writable", and a layout with a Users write ACE as `PER_USER`.
- **One module-top dispatch block.** `main.py` gains a block ABOVE `run_entry()` (today `main.py:32-37`) and above the heavy imports (`main.py:40-57`) that recognises exactly three argument families: `--veloapp-*`, `--update-service` and `--update-health-probe`. They are handled there and the process exits; `src.update.entry`, `src.utils.paths` and the ETL are never imported on that path (tripwire test on `sys.modules`). The existing `_cli` pre-checks (`--elevated-apply`, `--schedule-check`, `--diagnose`) are unchanged.
- **Hooks touch nothing.** `--veloapp-*` is handled in that block, before any profile, log, keyring or registry access. The hook handler calls `App().run()` and exits. Uninstall's hook does NOTHING to the profile, the secret, the nightly task or the HKLM machine-scope key (Goal 5). Pinned by tripwire tests (the profile, log and `winreg` seams raise if touched under a hook argument), with a positive twin that a hook still reaches `App().run()`.
- **The console line (S0b #3).** On a `MACHINE` install `App().run()` is called ONLY for `--veloapp-*` arguments and inside the SYSTEM updater (section 3). An ordinary window or CLI start never calls it, so a service-account nightly prints nothing extra. Apply-at-start is already off (0054 S5), so nothing is lost. Proven on the real build in L1.

### 2. Machine setup (provisioning at install time, not at the first schedule)

The MSI's dialogs cannot ask a question (S0b #1), and its hooks may run as SYSTEM, so the install itself only lays files. **The first-run Setup asks instead**, and a headless command does the same for scripted and Server Core districts:

```
DistrictSync.exe --machine-setup --updates off|notify|on [--grant <principal>]   (exit 0 / 1 with a plain reason)
```

**It runs unelevated like the window** (the app never runs elevated, `main.py:478-485`): the elevated half is the existing per-operation `--elevated-apply` child behind one UAC prompt, and the unelevated parent half (`complete_handover`: re-pin, fence, `MOVED.txt`) runs exactly as in the window, so an older copy cannot keep writing the per-user profile. **Remote sessions** (WinRM, SSH, Server Core) have no UAC prompt and an already-elevated token: whether the `ShellExecuteExW runas` + DPAPI CurrentUser handshake works there is [N] (L1, T6). If it does not, the design is a dispatch-first elevated entry (`--machine-setup` recognised beside `--elevated-apply`, before the preamble) that performs only the provisioning op, never the profile/log preamble; decided after L1.

It reuses plan 0049's provisioning op unchanged where it can (`provisioning.py` stays the ONLY registry writer): create `C:\ProgramData\DistrictSync` with its protected DACL, migrate the running admin's per-user profile into it (`migrate_profile`), seed the secret, commit the HKLM switch, and in addition write the update mode (section 3) and register or remove the SYSTEM updater task. In the window this is one UAC prompt from a first-run Setup step with the update question (default `notify`, O1). Today's provisioning trigger (a Schedule press with a foreign principal, `setup.py:3716-3731`) keeps working; on a `MACHINE` install it finds the profile already provisioned and only grants the principal.

**Headless schedule registration** (gap 6; none exists today): `DistrictSync.exe --schedule-register --time HH:MM --sis <cfg> --input … --output … --principal current|gmsa:<DOMAIN\name$>|password:<DOMAIN\user> [--wake]`, with the password from `DISTRICTSYNC_TASK_PASSWORD`, `--task-password-stdin` or a prompt, the same three routes `--sftp-configure` uses, and `--schedule-remove`. It drives the existing `Scheduler` protocol, principal model, schedule check and record (`AppConfig.schedule_*`): the CLI is another caller of the same functions, not a second implementation. The gate it shares with Setup, `register_block`, lives in `src/ui_flet/setup_gates.py` today; it moves to `src/scheduler/` first (pure function, no UI import) so the CLI does not depend on the UI layer. A password never reaches argv, the environment of a child, or a log (scheduler invariant).

### 3. Updates on a machine-wide install

**One mode, four sources, one reader.** `src/update/policy.py:effective_update_mode() -> (UpdateMode, Source)`:

| Mode | Meaning |
|---|---|
| `off` | No update connection at all. Help: "Automatic updates are off." |
| `notify` | The nightly fetches the signed manifest only (no package), after its run, and writes the result to `runs\update-notice.json` (the one folder it can write; display only, never read by SYSTEM; the window shows only a value that parses as a version, never free text from the file). Home/Help/`--diagnose` say "Version X is available (you have Y)". Nothing is downloaded or installed. |
| `on` | As `notify`, and the SYSTEM updater installs signed updates (below). |

Sources, first match wins: (1) **policy** `HKLM\SOFTWARE\Policies\myBlueprint\DistrictSync\UpdateMode` (REG_SZ, written only by Group Policy or an administrator; the app never writes it; when present the Settings control is disabled and reads "Set by your organisation"); (2) **machine setting** `HKLM\SOFTWARE\DistrictSync\UpdateMode`, written only through the elevated provisioning op (Settings toggle or `--machine-setup`); (3) the default, `notify` (O1). The env var / marker-file off switch from the shared package stays as an extra OFF that can only make it stricter. A malformed value is `off` and logged, never `on`. Both keys are read with `KEY_READ | KEY_WOW64_64KEY` (the 0049 lesson). Settings toggle and Help line are a `src/ui_flet` change and go through the `districtsync-design` skill. Shipped with ADMX/ADML templates under `deploy/group-policy/`.

**Why HKLM and not `config.json`:** the SYSTEM updater must decide from inputs only administrators can write. `config.json` sits in a folder the setup user can modify (`provisioning.py:410-449`), and a principal-writable switch read by SYSTEM would let that account turn on a privileged action.

**The SYSTEM updater (G2).** A task `\DistrictSync\DistrictSync Updater` as `NT AUTHORITY\SYSTEM`, registered by the provisioning op when the mode is `on` and deleted when it is not. It runs `current\DistrictSync.exe --update-service` daily at the nightly's time plus 60 minutes (and at boot + 15 minutes; `StartWhenAvailable` true for this task only, because a missed update night is harmless).

**The SYSTEM trust rule (review blockers 1-2).** Every input the updater reads is writable by Administrators/SYSTEM only. Concretely, `--update-service`:
- is recognised in the module-top block (section 1), before `run_entry()` and the heavy imports; it never calls `AppConfig.load()`, `user_data_dir()`, `pin_data_dir()` or the keyring, and opens **no file under `C:\ProgramData\DistrictSync`** (the setup user has Modify there, `provisioning.py:412-419`; the principal has Modify on `runs\`);
- reads the mode from the two HKLM keys only (the existing `update_enabled` in `config.json`, default `True` and principal-writable, is NOT an input on `MACHINE`; it keeps meaning only for per-user copies);
- reads the nightly's time from Task Scheduler (`task_com.read_task` on the registered task), not from `AppConfig.schedule_time` (today's `apply.minutes_to_next_run` reads `config.json`, `apply.py:44-57`, so it is not reused here);
- keeps its state and log in **`C:\ProgramData\DistrictSync-Updater\`**, outside the install root so an MSI major upgrade or uninstall (which deletes the install folder, S0b #6-7) does not erase the bad-version record or the log. It is created by the provisioning op through 0049's never-adopt `_create_directory_with_sddl` with a PROTECTED descriptor (SYSTEM and Administrators full, Users read/execute); an existing folder is never adopted (a pre-planted one makes machine setup refuse with a named cause). Before every open the updater re-checks: owner ∈ {SYSTEM, Administrators}, not a reparse point (folder and file), and no write-class ACE for any SID outside {SYSTEM, Administrators, TrustedInstaller}. (0049's `assert_machine_dir_trusted` is not reused as-is: it only rejects open-group aces, `paths.py:570-591`, and would pass a write ace for the principal's own SID.) Velopack's staging stays inside the install root (`packages\`, admin-only);
- a test enumerates every file, directory and registry key the updater opens (audit hook) and fails on anything outside that list; L1 pre-plants each location as a standard user and the updater must refuse.

Steps:
1. refuse (reason code, exit 0) unless the mode is `on`, the install kind is `MACHINE`, and the feed and key are configured;
2. refuse if any process runs from the install folder in any session (the nightly as its account, an admin's window), or the nightly's time is within 30 minutes; whether Velopack/the package can see other sessions' processes is [N] (L1), and our own check enumerates them as SYSTEM regardless;
3. fetch and verify the signed manifest (the shared package; host-locked, sequence, expiry), download the package into `C:\ProgramData\DistrictSync-Updater\staging\`, check its SHA-256 against the manifest, and hand **that exact verified file** to Velopack (no second download by Velopack: 0054 D3, a local folder holding only verified files). Where Velopack under SYSTEM stages its own copy is [N] (S0b #4 showed a per-profile `packages` folder for a user) and is checked in L1;
4. additionally check the Authenticode signature (subject `myBlueprint Corp.`, timestamped) of every `.exe`, `.dll` and `.pyd` in the package that carries one, and that `DistrictSync.exe`, `Update.exe` and `flet.exe` do; the manifest hash is the integrity check for unsigned payload files;
5. request Velopack's apply-after-exit with **no restart** (`restart=False`; a restart under SYSTEM would start the app as SYSTEM) and exit;
6. register a one-shot SYSTEM run of `--update-service` 10 minutes later (so the health probe and any restore happen the same night, well before the next nightly, not 24 hours later);
7. write one plain line per attempt to `C:\ProgramData\DistrictSync-Updater\updater.log` (readable by Users; written by SYSTEM only): time, from-version, to-version, result, reason code; no account, no path outside the install.

**Health and rollback on a machine-wide install (review blocker 3).** The new version's own updater cannot recover a version that cannot start, so recovery cannot depend on importing the app:
- `--update-service` runs the shared start guard's recovery **as its very first statement**, before any heavy import (the same position `main.py:26-37` gives `run_entry()`), so a build that fails at import still reaches recovery as long as the frozen bootloader and the guard module load.
- "Healthy" for a machine-wide install is decided by SYSTEM, not reported by the nightly: after an apply, the one-shot run (step 6) starts the new `current\DistrictSync.exe --update-health-probe` under supervision (bounded). The probe is dispatched in the module-top block, imports the whole module graph the nightly and the window need (ETL, delivery, Flet and the shell, without opening a window), touches no profile, log, pin, handshake folder or keyring, and writes only to stdout. (`--version` is NOT used: it runs the full CLI preamble, pinning the profile and opening a log in principal-writable `runs\`, `main.py:544-575`, which SYSTEM must never do, and it never imports Flet.) The result goes to `C:\ProgramData\DistrictSync-Updater\health.json`. The nightly's run record is never read by SYSTEM (principal-writable; review blocker 1).
- After two failed supervised probes (the one-shot run retries once) the updater restores the kept previous package (shared package `recover_broken_start`, `record_bad`), so the feed never offers that version again.
- **Unrecoverable, stated:** a release whose bootloader or guard module cannot load at all (no Python runs). The fallback is the district (or we) running the previous release's MSI; T12b tests that this works over a broken version. An alternative that closes it, a separate updater copy that Velopack does not replace, is recorded as option U3-b and decided after L1.
- Whether the shared package accepts a state folder the starting process cannot write, and a supervised-start health mode, is [N]; slice U3 changes `myb-desktop-shared` first (ticket there), and DistrictSync does not write its own copy.

**MSI and Velopack together (review major 11).** The hidden MSI product keeps the version it was installed with while Velopack moves the files forward (S0b #5). So: (a) running an MSI OLDER than the version Velopack applied is a silent downgrade; the IT guide says "run the newest MSI only", `--diagnose` prints both versions, and L1 tests whether a `LaunchCondition`-free downgrade can be refused by the app on first start (T24); (b) whether Windows Installer self-repair (advertised shortcuts, a resiliency check) can put the MSI's old files back after a Velopack apply is [N] (L1, T25); the pack uses non-advertised shortcuts if `vpk` allows it.

**Never during a run.** Covered by step 2 and Velopack's own refusal when files are in use [S0 #8].

### 4. No names in logs (owner, 2026-11-23)

Sites that write an account today [V, research 2026-11-23]: the machine-scope log file name `runs\etl_tool-<account>.log` (`paths.py:1018-1031`); the run record's `run_as` (`pipeline.py:1069`, also in the `__DISTRICTSYNC_RUN__` log line); the schedule-check marker's `account` (`schedule_check.py:378-383`); HKLM `ProvisionedBy` (`provisioning.py:352-354`); and per-user profile paths (`C:\Users\<name>\…`) that reach logs through the data-dir banner and provisioning/migration messages. N1 starts with a sweep that confirms this list (the review found the last item), so "every site" is a test, not a claim.

**The constraint (review majors 5-6).** Run History does not show a kind; it shows "This account" / "Another account" by comparing the record's `run_as` with the current account (`run_history.py:170-192`, `principal_key`), and the per-writer log name exists because two processes on one `RotatingFileHandler` tear each other's rotations (`paths.py:1023-1027`). A running process also cannot tell a gMSA from a password account without the task record (0049 S-4 forbids sniffing `$`). So the change keeps the comparison and the separation, without the name:
- **Account tag:** `account_tag(account) = HMAC-SHA256(install_salt, normalised account)[:12]`. `install_salt` is 32 random bytes created at machine setup in HKLM (admin-writable, readable by everyone, since every account must compute its own tag). It protects against someone holding a copied log or record, not against a person on the server, who can read Task Scheduler anyway. The alternative is the account's SID string (no name, but resolvable to one inside the domain): owner decision O5.
- Records store `run_as_tag`; Run History compares tags (`principal_key` gains a tag form); the KIND shown comes from the atomic schedule record (`schedule_run_as_kind`, 0049 S-4), never from inside the process.
- Logs: `runs\etl_tool-<role>-<tag>.log` (role `scheduled` / `interactive`), one writer per file as today.
- `ProvisionedBy` dropped, and deleted from HKLM by the next provisioning op on an existing 0049 install; its consumers move to `ProvisionedAt` plus "an administrator": `home_status.machine_scope_line`, `screens/setup.py:2116`, `diagnostics.py:62` and `:135`.
- **No salt yet** (a 0049 machine-scope install, including one-file copies, which keep shipping under G4): the first process that needs a tag and finds no salt does NOT create one (it has no right to write HKLM); it records the kind only and Run History shows "Unknown account" for that row until machine setup has created the salt. One-file and MSI copies share `account_tag()`, so they compute identical tags.
- **The role in the log name** (`scheduled` / `interactive`) is decided by an argv pre-scan for `--source scheduled` (or `DSYNC_SOURCE`) before the log sink opens (the sink opens at `main.py:563`, argparse parses `--source` later), pinned by a test.
- The marker carries the tag.
- `AppConfig.schedule_run_as_user` keeps the account (config needed to re-register, not a log); `--diagnose` keeps its declared "not PII-free" status, prints the kind and tag, and says the account is in Task Scheduler.
- Existing log files and `history.db` rows are left as they are; only new writes change. Every consumer of `run_as` (`run_history.run_as_display`, `components.run_table`, `schedule_probe.foreign_task_account` which reads the RECORD, the S-2a suppression rule) is listed in the slice with a test.
- A scan test runs a scheduled run, the window, machine setup and a migration on a fixture account with a distinctive name and fails if the name appears in any file WRITTEN by those runs, with `config.json`'s `schedule_run_as_user` as the one declared exception (pre-existing logs and `history.db` rows are out of scope by design).

### 5. Migration

`DistrictSync.exe --migrate` (unelevated parent with per-operation elevated children, like `--machine-setup`; the window offers the same as a Setup band when it finds an old copy), on a `MACHINE` install:
1. **Find** what is there: one-file copies and per-user installs referenced by DistrictSync tasks (`DistrictSync_*` actions, read through `task_com`), the running admin's per-user profile, a per-user Velopack install under any profile it can read.
2. **Profile, history, settings:** machine setup (section 2) migrates the RUNNING admin's per-user profile (`migrate_profile`: `config.json`, `known_hosts`, mappings, `history.db`; skip-if-present, never deletes the source). A service account's own per-user profile is out of reach by design and is reported, not copied.
3. **The delivery secret (review blocker 4).** A headless service-account district seeded the SFTP password into the SERVICE ACCOUNT's own keyring by running `--sftp-configure` as it (`headless-sftp-setup.md:221-226`), and its SFTP settings may live only in that account's profile; `migrate_profile` and `_seed_secret` read the admin's. So when the nightly's principal is not the admin, `--migrate` requires the SFTP settings and password for the machine store (the same three routes as `--sftp-configure`, or "already in the machine store"), then runs the `--sftp-test` logic under the machine scope; **it does not touch the nightly until that test passes.**
4. **The nightly (review major 9).** The task name is fixed (`DistrictSync_Daily`, `windows.py:88`), and changing an existing task's action in place is the shape the owner ruled out (`setup_gates.py:185-188`). So: read the old task's facts (`task_com.read_task`: principal, kind, time, arguments, wake), including for a district that made the task with raw `schtasks` and has no `AppConfig.schedule_*` record; refuse to start within 30 minutes of the nightly's time; collect everything needed BEFORE changing anything (the principal's password for a stored-password principal, by prompt, env or stdin; whether it can be avoided is [N], L1); run the schedule check's prerequisite probe for the new program path as the principal (the DSYNC-33 one-shot check task, pointed at `…\current\DistrictSync.exe`); then delete the old task, confirm MISSING, and register `DistrictSync_Daily` with the same facts and the new path through the existing register path. If that register fails, the OLD task is re-registered from the facts already read (same principal, password in hand, old path), and the report says so. There is never a second enabled nightly (no temporary task: two tasks with one trigger time would double-deliver), and the only window without a task is the seconds between delete and register, far from the run time. Delete-then-create is kept, so no DECISIONS exception is needed.
5. **Old copies:** a per-user Velopack install is uninstalled with its own `Update.exe uninstall --silent` only when run as that user (otherwise reported with the one command to run); a one-file exe is never deleted by us (it may be referenced by the district's own scripts); the report names it.
6. **Report**, printed (and in the app's log): what was found, what moved, what was left and why, the next step if any. Exit 0 only when the nightly runs the installed copy and the delivery test passed.

Scripted districts get the same steps as documented commands: `msiexec /i DistrictSync.msi /qn /l*v install.log`, `DistrictSync.exe --machine-setup …`, `--migrate` or `--schedule-register …`, `--diagnose`.

### 6. The early guard (gap 3, independent of v2)

`setup_gates.register_block` (`src/ui_flet/setup_gates.py`) gains `INSTALL_NOT_SHAREABLE`: a foreign principal (`principal_key != ""`) running from a per-user Velopack layout (`install_root() is not None` today; M1 switches the predicate to `install_kind() == PER_USER`) is refused before any UAC prompt, with copy that names the fix ("This copy is installed for your account only, so another account cannot run it. Use the machine-wide installer or the single-file download."). The headless `--schedule-register` uses the same gate. A positive twin pins that the signed-in principal still registers on a per-user install, and that the one-file exe and a `MACHINE` install are not blocked. This ships first, before v2, on the current code.

### 7. Logging and `--diagnose`

- MSI: the docs give `msiexec … /l*v "%ProgramData%\DistrictSync\install-<version>.log"`; the release body says where Velopack's own log is (`%LOCALAPPDATA%\velopack\` of whichever account ran it, `C:\Windows\System32\config\systemprofile\…` for SYSTEM).
- Updates are recorded by SYSTEM in `C:\ProgramData\DistrictSync-Updater\updater.log` (section 3); installs by the MSI log; migration by its printed report. No separate install-history file (cut in review: it restated these and the nightly cannot write the profile root).
- `--diagnose` gains an "Installation" block: install kind, folder, running version and the MSI product's version (they differ after a Velopack apply, section 3), update mode and its source (policy / machine setting / default), updater task state and next run, last check and last apply (from `updater.log`), "behind by" when known, any old copies or a nightly still pointing elsewhere.
- **After uninstall** both tasks point at a missing program and no DistrictSync is left to say so. The uninstall guide says to Remove the schedule first (or reinstall: the nightly resumes, T18); hooks never delete anything, the updater task included (an MSI major upgrade runs the old product's uninstall, so a hook that deleted the task would silently switch updates off at every MSI upgrade). A leftover updater task after a real uninstall fails harmlessly ("program not found"), because nothing can be planted in Program Files; the uninstall guide gives the one command to remove it, and reinstalling re-uses it. Copy is quoted by the partner docs, so the strings are single-sourced and registered in the doc-parity tests.

### 8. One-file end of life (G4)

The one-file exe keeps shipping. From the first MSI release it shows one Home band and one `--diagnose` line: "A machine-wide installer is available; this download will be retired." The migration ledger (district codes only, no names: setup kind and the date `--diagnose` confirmed migration) lives on the R3 Jira ticket, not in the repo (review: a tracking list, not a doc). Retirement is slice R3: when the owner confirms the ledger is complete, the release stops attaching the asset, and the partner docs' `releases/latest/download/DistrictSync-windows.exe` links have already moved.

## Test matrix (every row on a real machine; L = Azure lab, P = owner's PC, R = GitHub Windows runner)

| # | Scenario | Where | Pass means |
|---|---|---|---|
| T1 | Fresh MSI install, signed-in admin, window + Convert + Schedule (signed-in principal) | P, L | installed in Program Files, profile in ProgramData, nightly fires and records |
| T2 | Same, headless `msiexec /qn` + `--machine-setup` + `--schedule-register` | R, L | exit codes as documented; `--diagnose` shows everything |
| T3 | Stored-password local service account nightly | L | task fires as the account from Program Files, no console noise, run record carries a tag and the kind shows `service account` |
| T4 | Domain service account (password) | L | same |
| T5 | gMSA nightly, including the DSYNC-33 schedule check | L | check HEALTHY; nightly runs; Remove revokes |
| T6 | Server Core: MSI, machine setup, schedule, nightly, `--diagnose` (no window), over console, WinRM and SSH | L | all headless steps succeed in each session type, or the refusal says what to do |
| T7 | Upgrade from the one-file exe for each principal kind (signed-in, local password, domain password, gMSA), window and headless | L | settings, history, SFTP settings + secret (machine store) and task carried; `--sftp-test` passes as the principal before the task moves; old exe reported, not deleted |
| T7b | Same, where the service account seeded its own keyring and the admin's profile has no SFTP settings | L | migration asks for them, tests delivery, then moves the nightly; never a night with exit 3 |
| T7c | Migration interrupted at each step (kill the process), and a register failure after the delete | L | exactly one enabled nightly afterwards (the new one, or the old one re-registered); never two |
| T8 | Upgrade from the v3.30 per-user `Setup.exe` install (the SD74 shape) | P, L | per-user copy removed as its user; task re-pointed; profile carried |
| T9 | Upgrade over an installed copy: MSI N → MSI N+1 | R, P, L | major upgrade, same folder, nightly and updater tasks still valid, the updater's health and bad-version record kept, profile untouched |
| T10 | Updater on: SYSTEM applies N → N+1 after the nightly | L | applied; nightly result unchanged; `updater.log` line |
| T11 | Updater refuses while the nightly runs, and inside the keep-clear window | L | refusal code logged; version unchanged |
| T12 | A version that fails at start and one that fails at import are rolled back and never offered again | L | recovery by the SYSTEM updater's guard, before the app imports |
| T12b | A version whose bootloader cannot start at all | L | the previous release's MSI installed over it restores a working nightly (the documented fallback) |
| T13 | Mode `off`: zero connections (packet capture) | L | no connection by any DistrictSync process |
| T14 | Mode `notify`: manifest only, "behind" shown, nothing downloaded | L | capture shows one manifest fetch |
| T15 | Policy key set by Group Policy (`off` with setting `on`, and `on` with setting `off`) | L | policy wins; Settings disabled with its caption; app never writes the policy key |
| T16 | Offline / feed blocked | L | runs as normal; `unreachable` reason; no retry storm |
| T17 | Uninstall (Apps & features and `msiexec /x`) | R, P, L | program gone; profile, secret, nightly task and HKLM machine-scope key kept; updater task removed or left inert (reported) |
| T18 | Reinstall after uninstall | P, L | nightly runs again with no reconfiguration |
| T19 | Group Policy software install and Intune deployment (SYSTEM context) | L | install hook creates no `systemprofile` profile |
| T20 | Non-admin user opens the window on a machine-wide install | L | 0049's refusal and grant window, worded for this case; no UAC by Velopack; no Velopack files in their profile |
| T21 | Signature checks: MSI, installed exes, update package files | R, L | all valid with timestamp; a package whose hash or signature does not match is refused before Velopack sees it |
| T22 | SmartScreen / mark-of-the-web on a fresh MSI download | L | result recorded on the release ticket |
| T23 | A second administrator (filtered token) opens the window | L | same as T20; documented |
| T24 | An MSI older than the version the updater applied is run | L | documented outcome; `--diagnose` shows both versions; the app's first start refuses or warns (decided in L1) |
| T25 | Windows Installer repair / resiliency after an updater apply | L | the applied version stays |
| T26 | A standard user pre-plants each folder and key the SYSTEM updater reads, and swaps in a junction | L | the updater refuses; nothing is written outside the install |

## Affected files

| Path | Change |
|---|---|
| `src/utils/install_kind.py` (new) | the ONE install-kind reader |
| `src/update/startup.py`, `src/update/entry.py`, `src/main.py` | hook dispatch first; `App().run()` only for hooks / updater on `MACHINE`; start guard per kind |
| `src/scheduler/task_com.py` | `runs_from_user_writable_install` reads the install kind |
| `src/ui_flet/setup_gates.py` (moves to `src/scheduler/` in M3), `src/ui_flet/screens/setup.py`, `src/ui_flet/setup_errors.py` | `INSTALL_NOT_SHAREABLE`; first-run machine setup step; migration band |
| `src/scheduler/provisioning.py`, `elevated_apply.py`, `provision_session.py` | update mode write, updater task register/remove, no `ProvisionedBy` |
| `src/update/policy.py`, `src/update/service.py` (new), `check.py`, `status.py` | effective mode; `--update-service`; notify-only check; Help/Home lines |
| `src/cli/` (new: `machine_setup.py`, `schedule_cli.py`, `migrate.py`) | headless commands over the existing functions |
| `src/utils/paths.py`, `accounts.py`, `src/etl/pipeline.py`, `schedule_check.py`, `diagnostics.py` | role-named logs; principal kind in records; Installation block |
| `deploy/group-policy/DistrictSync.admx`, `en-US/DistrictSync.adml` (new) | policy templates |
| `.github/workflows/release.yml` | MSI pack, sign, verify, runner install/upgrade/uninstall smokes; later attach the MSI; later retire per-user Setup |
| `scripts/ci_flet_pack_smoke.py` | `--installed-machine` kind |
| `docs/developer/installer-lab.md` (new) | the lab runbook: `az` commands to create and delete the VMs, the gMSA and service-account set-up, and the matrix steps (automation only if the matrix is rerun more than twice; review cut) |
| `tests/` | per slice |
| `docs/` | `DECISIONS.md`, `INVARIANTS.md`, `CLAUDE.md` (rules only), `developer/updates.md`, `machine-scope.md`, `scheduler.md`, `release.md`, new `developer/installer-migration.md`, partner `installation.md`, `headless-sftp-setup.md`, `managed-service-accounts.md`, new `partner/it-deployment.md` (msiexec, GPO, Intune, ADMX), `troubleshooting.md`, `faq.md`, `sd74-hosted-server.md`, `CHANGELOG.md` |

## Risks & mitigations

| # | Risk | Mitigation |
|---|---|---|
| R1 | A SYSTEM process installing code from the internet is the security concern districts raised. | Off by default (G1); policy that wins; signed manifest AND Authenticode on the package's executables before Velopack sees it; staging only in an admin-only folder; inputs SYSTEM reads are admin-writable only; feed host pinned (`ALLOWED_UPDATE_HOSTS`). Stated in the IT deployment guide so a district's security review can read it. |
| R2 | A principal-writable file read by SYSTEM becomes an escalation (staging, config switch, health state). | Designed out (sections 3, health): every SYSTEM input is in HKLM or an admin-only folder; a test enumerates the updater's inputs and asserts each one's ACL in the lab. |
| R3 | Velopack's MSI is new in Velopack and fixed in shape (no custom dialogs or actions). | We need no custom action (provisioning is the app's). If a later Velopack changes the MSI, the version pin and the B1 rehearsal catch it. |
| R4 | A hook running as SYSTEM or impersonated touches a profile. | Hooks dispatched before anything (section 1), tripwire tests, T19. |
| R5 | Re-pointing a password-principal task needs the password, which IT may not have to hand. | Migration never deletes the old task without a replacement; L1 tests whether the action can change without the password. |
| R6 | Machine-wide changes the meaning of `runs_from_user_writable_install` and the start guard, which many tests pin per-user (`tests/test_scheduler_runas.py:1568-1612`, `conftest.py:795-830`, `test_installed_flavour.py`). | The install-kind slice updates those pins deliberately, each with a `MACHINE` twin. |
| R7 | Two Windows installers (per-user Setup and MSI) on releases for a while. | The per-user Setup is retired from releases in R2, the release after the MSI's first; per-user copies keep working and are migrated. |
| R8 | The shared package may not support an admin-only state folder or cross-session process detection. | Slice U3 is a change in `myb-desktop-shared` first; DistrictSync does not fork the logic. |
| R9 | Azure lab cost and drift. | Scripted create/destroy; deleted after each matrix run; nothing district-shaped (no real data) on it; synthetic fixtures only. |
| R10 | Group Policy / Intune behaviour differs from a manual `msiexec`. | T19 in the lab before the IT guide claims it. |
| R11 | Districts on the one-file exe never migrate. | G4 keeps it published; the band and the ledger make the gap visible; nothing breaks. |

## Decomposition (each slice: one Jira ticket, one branch, one PR; Jira drafts below)

- [ ] **G0. Guard: no foreign principal on a per-user installed copy** (section 6). Small, on today's code, ships before v2. *Tests:* gate units + positive twins; the produced-vs-classified copy sweep stays green.
- [ ] **L1. Azure lab and real-build spike (evidence only).** The runbook (`docs/developer/installer-lab.md`) creates a domain controller, a Server 2022 member with Desktop Experience and a Server Core member, with a gMSA and a local service account; the real DistrictSync MSI is built unsigned from a dispatch run; every [N] in this plan is answered (S0b's "not proven" list, remote-session elevation, password-task re-point, Velopack staging under SYSTEM, extra folder at the install root surviving apply, cross-session process detection, self-repair, uninstall hook rights); the lab is deleted. *Output:* findings appended here. Needs O4.
- **U3 gating rule (not a slice):** the `on` mode does nothing on a machine-wide install until U3 has landed and T12/T12b pass, even after the feed exists.
- [ ] **B1. MSI in the release rehearsal.** `vpk pack --msi --instLocation PerMachine`, sign, verify (MSI + installed exes), install on the runner machine-wide, CLI + window smokes on the Program Files copy, MSI N→N+1 upgrade, uninstall asserting T17. Dispatch only, nothing published. *Tests:* workflow-shape tests; a rehearsal run quoted.
- [ ] **M1. Install kind and safe hooks.** `install_kind()`, hook-first dispatch, `App().run()` gating, `runs_from_user_writable_install` and the start guard read the kind; machine-wide refuses a per-user profile. *Tests:* tripwires with positive twins; the R6 pins updated with `MACHINE` twins.
- [ ] **M2. Machine setup.** `--machine-setup` and the first-run Setup step over the existing provisioning op; HKLM update mode write; `ProvisionedBy` dropped. UI slice → design skill. *Tests:* provisioning units on the new payload keys; elevated-child request validation; UI definition of done.
- [ ] **M3. Headless schedule commands.** `--schedule-register` / `--schedule-remove` over the existing scheduler functions, password routes as `--sftp-configure`. *Tests:* the password never in argv/env/log (existing invariant tests extended); same gates as Setup (G0 included).
- [ ] **U1. Update mode and policy.** `effective_update_mode`, policy + machine keys, `notify`, Settings toggle (disabled under policy), Help/Home/`--diagnose` lines, ADMX/ADML. *Tests:* source precedence table; malformed → off; zero connections in `off` (audit-hook test) with an eligible twin.
- [ ] **U2. The SYSTEM updater.** `--update-service`, task register/remove from the provisioning op, refusals, admin-only staging, Authenticode check, `updater.log`. *Tests:* refusal table; staging path ACL assertion; no non-SYSTEM caller can reach Velopack apply on `MACHINE` (pinned); e2e on the e2e flavour run as SYSTEM in the lab.
- [ ] **U3. Machine-wide health and rollback** (shared package first, ticket in `myb-desktop-shared`; then DistrictSync wiring). *Tests:* T12 in the lab; the start guard's state not writable by the principal.
- [ ] **N1. No names in logs.** Section 4. *Tests:* every listed site pinned; a log-line scan over a scheduled run's output finds no account.
- [ ] **D1. Migration.** `--migrate` and the Setup band (section 5). *Tests:* units on discovery and the never-lose-the-nightly rule; T7, T8 in the lab.
- [ ] **D2. Install logging and `--diagnose` Installation block** (section 7). *Tests:* doc-parity registrations for quoted strings.
- [ ] **R1. First MSI release.** Attach `DistrictSync.msi` to the release with its checksum line; partner docs (`it-deployment.md`, installation, headless, gMSA, troubleshooting, SD74 runbook); QA checklist rows; the full matrix run in the lab and recorded on the release ticket. The one-file exe and per-user Setup still ship.
- [ ] **R2. Retire the per-user `Setup.exe` from releases**, the release after R1. Per-user copies keep working and get D1's band.
- [ ] **R3. Retire the one-file exe** (G4), when the owner confirms the migration ledger is complete.

**Order.** G0 now (independent). L1 and B1 next (evidence before code). Then M1 → M2 → M3, U1 → U2 → U3, N1 and D2 in parallel with them, D1 after M2/M3, R1 after the matrix passes, R2, R3. Automatic updates (`on` doing anything) additionally wait on plan 0054 S6b/S8; every other slice does not.

### Jira tickets (project DSYNC, under epic DSYNC-4; created 2026-11-23)

| Slice | Key | Type | Summary |
|---|---|---|---|
| plan | DSYNC-90 | Task | Plan 0055: installer v2, machine-wide MSI replacing the one-file exe (S0b spike evidence) |
| G0 | DSYNC-91 | Bug | Setup refuses a service-account/gMSA schedule on a per-user installed copy |
| L1 | DSYNC-92 | Task | Azure test lab (DC, member, Server Core) and real-build machine-wide spike |
| B1 | DSYNC-93 | Task | Release rehearsal builds, signs and smoke-tests a per-machine MSI |
| M1 | DSYNC-94 | Task | One install-kind reader; Velopack hooks run before anything touches a profile |
| M2 | DSYNC-95 | Task | Machine setup at first run and `--machine-setup` (ProgramData profile, update mode) |
| M3 | DSYNC-96 | Task | Headless `--schedule-register` / `--schedule-remove` over the existing scheduler |
| U1 | DSYNC-97 | Task | Update mode off/notify/on with an HKLM Group Policy override and ADMX templates |
| U2 | DSYNC-98 | Task | SYSTEM "DistrictSync Updater" task applies signed updates on machine-wide installs |
| U3 | DSYNC-99 | Task | Start-guard health and rollback for machine-wide installs (shared package + wiring) |
| N1 | DSYNC-100 | Task | No account names in logs, log file names or run records |
| D1 | DSYNC-101 | Task | `--migrate` and a Setup band: one-file and per-user installs move to the machine-wide install |
| D2 | DSYNC-102 | Task | Install/upgrade/update records and a `--diagnose` Installation block |
| R1 | DSYNC-103 | Task | First release with `DistrictSync.msi`; IT deployment guide; full lab matrix recorded |
| R2 | DSYNC-104 | Task | Stop attaching the per-user `DistrictSync-Setup.exe` |
| R3 | DSYNC-105 | Task | Retire `DistrictSync-windows.exe` once the migration ledger is complete |

## Owner decisions, round 2 (2026-11-23, approved: "i trust your best judgement. proceed", relayed by the orchestrator session)

| # | Decision |
|---|---|
| O1 | **Default mode `notify`**: the signed manifest only, "you are behind" shown, nothing downloaded. **Strict `off`** (no connection at all) exists in Settings and in the HKLM policy key, which the app cannot override. The owner named outbound update traffic from internal servers as the security concern. |
| O2 | The update question is asked at first-run Setup and by `--machine-setup --updates`, not by the MSI. |
| O3 | Install folder: Velopack's default, `C:\Program Files\myBlueprint.DistrictSync\`. |
| O4 | Azure test VMs run from the runbook's `az` commands under the owner's login, in `customer-experience-ca` or a dedicated lab resource group: small sizes, tagged, shut down when idle, deleted when the matrix is done; the monthly cost is named to the owner before anything costly is created. |
| O5 | Run History tells accounts apart with the per-install keyed tag, not a SID. |
| O6 | A release that cannot start at all is fixed by installing the previous MSI; no separate updater copy. |

---

## Review

**2026-11-23, two independent reviewers, given the code and this plan only (not the author's reasoning).**

**Refuting review: CHANGES REQUIRED, 4 blockers / 9 majors / 3 minors. All addressed in this revision:**

| # | Finding | Resolution |
|---|---|---|
| B1 | SYSTEM updater would read principal-writable inputs (`config.json` for the nightly time and `update_enabled`, `apply.py:44-57`; the run record for health) | "The SYSTEM trust rule": HKLM, Task Scheduler and SYSTEM-only folders as the only inputs; enumerated-inputs test; T26 |
| B2 | Updater folders under ProgramData could be pre-planted or junctioned | Protected never-adopt `C:\ProgramData\DistrictSync-Updater\` (round 2); trust re-check before each open; T26 |
| B3 | Rollback ran inside the version that cannot start | Guard recovery first in `--update-service`; SYSTEM-supervised health; T12/T12b; O6 for the residue |
| B4 | Headless service-account districts would lose SFTP delivery (per-account keyring) | Migration step 3: machine-store secret + delivery test before the nightly moves; T7/T7b |
| M5-6 | "No names" broke Run History's this/another comparison and the per-writer log rule | Keyed account tag; kind from the schedule record; role + tag log names; consumers listed; O5 |
| M7 | Machine scope locks other users out; T20 as written could not pass | 0049 access model kept and stated; T20 rewritten; T23 added |
| M8 | "(elevated)" headless commands contradicted the never-elevated rule | Unelevated + per-op child; remote-session behaviour [N] with a dispatch-first fallback; T6 per session type |
| M9 | Re-pointing collides with the fixed task name and the delete-then-create rule | (Round 1: temporary task; replaced in round 2 by delete, register, or re-register the old task) T7c |
| M10 | Path-based install kind broke custom folders | ACL-based detection; `D:\` test |
| M11 | MSI downgrade, self-repair, restart under SYSTEM | Section "MSI and Velopack together"; `restart=False`; T24, T25 |
| M12 | Authenticode on three exes left payload unchecked; possible TOCTOU | Manifest hash on the exact file handed to Velopack; Authenticode on all signed files; staging location [N] |
| M13 | Fresh install still touches per-user folders | Declared exceptions listed; CLI runs the parent half |
| m14 | Wrong references (`install_root` line; `setup_gates` is in `src/ui_flet`) | Fixed; gate moves to `src/scheduler` in M3 |
| m15 | G0 depends on M1; notify state had no writable home; install-history unwritable by the nightly; post-uninstall tasks | G0 uses `install_root()`; `runs\update-notice.json`; install-history cut; uninstall paragraph |
| m16 | "Every site" over-claimed | N1 starts with a sweep; scan test over all logs |

**Scope review (YAGNI): OVER-BUILT. Taken:** `install-history.log` cut; `migration.log` cut (printed report); migration ledger moved to the R3 ticket; lab automation replaced by a runbook. **Not taken, with reason:** `notify` mode (the brief asks for a "you are behind" notice when off; O1 decided: default `notify`); ADMX templates (the owner asked for a Group Policy enforceable key, and the template is what makes it settable in the GPO editor; small, kept in U1); headless schedule commands (brief gap 6: scripted districts; also what Server Core needs); U3 rollback (the brief asks for rollback; automatic updates stay inert until U3 lands).

**Second-round refuting review (same day, on the revision): CHANGES REQUIRED, 2 blockers / 5 majors / 3 minors; first-round resolutions B4, M7, M10-M13, m14-m16 confirmed. All addressed:**

| # | Finding | Resolution |
|---|---|---|
| 1 | SYSTEM's `--version` health start runs the full preamble and opens a log in principal-writable `runs\` (`main.py:544-575`); never imports Flet | Dispatch-first `--update-health-probe` that imports the module graph and touches nothing |
| 2 | "Beside `--schedule-check`" is after `run_entry()`, which can create a `systemprofile` profile | One module-top block above `run_entry()` for hooks, the updater and the probe; `sys.modules` tripwire |
| 3 | `assert_machine_dir_trusted` would refuse an inherited folder and pass a write ace for the principal | Protected never-adopt creation; explicit trusted-SID write check |
| 4 | Install-kind ACL predicate underspecified; a `D:\` MSI install inherits Authenticated Users Modify | Named SID set, INHERIT_ONLY ignored; machine setup hardens a custom root; real-DACL fixtures |
| 5 | MSI upgrade deletes in-root updater state; a hook deleting the updater task switches updates off | State moved to `C:\ProgramData\DistrictSync-Updater\`; hooks delete nothing; T9 extended |
| 6 | A temporary migration task with the same trigger could double-deliver | No temporary task; facts and password collected first; delete, register, or re-register the old one; T7c "exactly one" |
| 7 | Health known only 24 h after an apply | One-shot SYSTEM run 10 minutes after the apply |
| 8 | Name-scan test contradicts kept `config.json`; missing `ProvisionedBy` consumers; no-salt case | Scope to new writes; `diagnostics.py` added; `ProvisionedBy` deleted on re-provision; "Unknown account" until a salt exists |
| 9 | Log role known only after argparse | argv pre-scan, pinned |
| 10 | `--migrate` still said elevated; notify file is principal-writable | Unelevated parent; render only a parsed version |

## Spec

Per slice, after the owner approves the plan.
