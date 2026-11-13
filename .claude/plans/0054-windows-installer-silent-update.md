# 0054 — Windows installer with silent, signed auto-update (Velopack)

- **Status:** Draft 2026-11-13. Awaiting owner approval. No product code and no workflow is changed by this plan.
- **Roadmap item:** Jira **DSYNC-16** "Evaluate a Windows installer (alongside the signed single exe)", parent epic DSYNC-4 "Distribution & trust". This plan is that ticket's stated output ("go/no-go and a scoped plan"). Each build slice below gets its own DSYNC ticket under DSYNC-4 when approved.
- **References:** plan 0041 (`.claude/plans/0041-legit-distribution-com-scheduler-onedir-installer.md`, Slices 2-3 still open) · `docs/DECISIONS.md` 2026-08-05 and 2026-08-05 (later), 2026-09-02 (signing lives in `release.yml`), 2026-11-04 (DSYNC-56) · `.github/workflows/release.yml`, `flet-pack.yml` · `docs/developer/release.md` · RosterGate (private repo, commit `e375646`): `docs/RELEASE.md`, `docs/GAPS.md` G-067..G-074, `DECISIONS.md` 2026-11-12, `src/rostergate/services/update.py`, `tools/sign_manifest.py`, `.github/workflows/release.yml`, and `spikes/updater/README.md` on its branch `m1b-updater-spike` (commit `2a2a30a`; the file is not on RosterGate `main`).

**Evidence labels used throughout.** **[V]** read in this repo in this session. **[RG-D]** demonstrated in RosterGate's spike (its own label D). **[RG-R]** read by RosterGate in documentation, not run. **[RG-code]** read in RosterGate's merged M8 code. **[I]** my inference. **[N]** not verified by anyone; a spike must settle it.

## Problem

Districts get DistrictSync as a signed one-file exe they download by hand, and they stay on whatever version they downloaded until someone replaces the file. Three costs follow.

1. **Fixes do not reach districts.** A released fix is "available, not deployed" (`docs/developer/release.md`, Fix versions) [V]. The partner population sets up once and never opens the app again (DECISIONS 2026-07-21) [V], so nothing prompts them to update.
2. **The one-file shape keeps the antivirus-shaped behaviours** plan 0041 named: `%TEMP%` self-extraction and the first-launch drop of `flet.exe` into `%USERPROFILE%\.flet\` (0041 Problem 2-3; DECISIONS 2026-08-05 later: "the one-file variant permanently keeps" them) [V].
3. **The scheduled task bakes in wherever the exe was launched from**, usually Downloads (0041 Problem, secondary) [V].

Plan 0041 approved an Inno Setup, per-machine installer for (2) and (3) and explicitly left auto-update out ("update checks need egress beyond the three allowlisted SFTP hosts, separate owner posture decision") [V]. The owner has now made that decision, for both department tools.

### Decisions already made by the owner (cited, not re-opened)

| # | Decision | Source |
|---|---|---|
| D1 | The installer is an ADDITIONAL release format. The signed one-file Windows exe, the macOS dmg and binary, the Linux binary and the GitHub Release page all stay. | Owner brief 2026-11-12; consistent with DECISIONS 2026-08-05 (later) [V] |
| D2 | Velopack installs and updates: per-user, no admin, silent, delta updates. It needs a one-folder PyInstaller build (`flet pack -D`) and `velopack.App().run()` first in `main`. | Owner brief; RosterGate DECISIONS 2026-11-12 [RG-code] |
| D3 | Our own signed update manifest sits in front of Velopack. The app downloads from our feed, verifies the signature, SHA-256 per file, expiry and no-downgrade, then hands Velopack a local verified folder. Velopack never touches the network. | Same |
| D4 | Signing is the mechanism `release.yml` already uses: tag-only, `release` environment, OIDC `azure/login`, Azure Trusted Signing, sign then verify then re-smoke, never sign pull request code. With Velopack: `vpk pack --azureTrustedSignFile` after the same login. | Same; `release.yml` `sign-windows` [V] |
| D5 | Feed: Azure Blob, storage account `mybpdesktopupdates`, container `districtsync` (exists, private; making it public-read is the owner's step). Only the release job uploads, with its OIDC login. Key Vault `mybp-desktop-signing` exists and holds no key. | Owner brief; RosterGate `docs/RELEASE.md` names table [RG-code] |
| D6 | RosterGate's M8 work is the reference implementation and the department wants one standard. | Owner brief |

**These decisions supersede parts of plan 0041 and must be recorded in `docs/DECISIONS.md` at approval** (with `Supersedes:` lines): 0041 Approach B's Inno Setup and per-machine `Program Files` install; 0041's rejection of a per-user install ("re-arms profile-execution patterns"); 0041's non-goal "auto-update". 0041's Goal 4 (ship the Flet client inside the install tree, never consult `~/.flet`) is NOT superseded and is carried into this plan.

## Goals / Non-goals

**Goals**

1. A signed `DistrictSync-Setup.exe` on every GitHub Release, beside every asset published today.
2. An installed copy that updates itself silently from the `districtsync` feed, without ever delaying, blocking or changing the result of a conversion.
3. Nothing is installed that was not signed by us twice: Authenticode on every executable, and our manifest signature over every update file.
4. A bad update that cannot start is rolled back locally and never offered again; a bad update that starts is stopped from spreading by one manifest publish.
5. The release workflow keeps every existing guarantee (D4) and gains a rehearsal that proves the installed, signed app without publishing anything.
6. The updater code is the same code RosterGate runs, by a mechanism that cannot drift silently (open decision O1).

**Non-goals**

- Removing or renaming any current asset, or changing the macOS and Linux builds.
- Auto-update for the one-file exe, macOS or Linux.
- Updating machine-scope (plan 0049) installs: see Risk R1. They stay on the one-file exe in this plan.
- An update prompt, changelog screen or "restart now" UI. Updates are silent (D2).
- Content packs (RosterGate's second use of the manifest format).
- Any Azure change, GitHub variable or secret change, release or tag. Those are owner steps listed under "Needs the owner".

## Approach

### 1. What is added, what is unchanged

| Thing | Today [V] | After this plan |
|---|---|---|
| `DistrictSync-windows.exe` (signed one-file) | published | **unchanged**, same job, same name, same permalink |
| `DistrictSync-macos.dmg`, `DistrictSync-macos`, `DistrictSync-linux` | published | **unchanged** |
| `SHA256SUMS.txt` | 4 files | gains one line for `DistrictSync-Setup.exe` |
| GitHub Release page | downloads table + signing note | gains one row; the one-file row stays |
| `DistrictSync-Setup.exe` (Velopack, signed) | absent | **new** asset on the Release |
| Update feed (`*.nupkg` + `manifest.json` on Azure Blob) | absent | **new**, not on the Release page |
| CLI, exit codes, output, profile location, keyring service | as documented | **unchanged** in both Windows builds |

The same source tree builds both Windows artifacts. A build-time stamp decides which one a binary is (section 3), so the one-file exe never loads Velopack and never checks for updates.

### 2. The one-folder Windows build, alongside the one-file build

**Build.** A second `flet pack` in the Windows leg of `flet-pack.yml`, with `-D` (one-folder), a separate `--distpath dist-onedir` and work path, and the same hidden-import list. `flet pack --onedir` exists natively in flet-cli 0.85.3 (0041 Approach B) [V in 0041, RG-D for `flet pack -D`]. The hidden-import list must not be written twice: it moves into one place both invocations read (a small `scripts/flet_pack_args.py` or a YAML anchor), with a parity test, because "never duplicate config" and the "literal copied into CI needs a parity test" rule both apply [V CLAUDE.md].

**The Flet client goes inside the folder (0041 Goal 4, carried).** One-folder alone does not stop the first-launch extraction into `~/.flet`; 0041's Round-1 review verified that against flet_desktop 0.85.3 [V in 0041]. The Windows one-folder job extracts the client into `_internal/flet_view/` and the frozen launcher sets `FLET_VIEW_PATH` to it. Then `flet.exe` is a file in our tree that `vpk pack` signs, and the quarantine-brick mode is unreachable for installed copies. RosterGate instead collects Flet whole (`--collect-all flet --collect-all flet_desktop`, `tools/build_app.py`) and has not run that build (G-069) [RG-code]; whether its folder still drops into `~/.flet` is [N]. This is a place the two tools differ today and one standard should be picked (question Q7).

**What this does to `flet-pack.yml`.** One new boolean input, `windows_onedir` (default false). `release.yml` and a manual `flet-verify` dispatch set it; a pull request does not, and pull requests pack Linux only anyway (DSYNC-56) [V], so **pull request CI time does not change**. The workflow keeps `contents: read` and takes no secrets: the one-folder tree is built and smoked unsigned there and signed only in `release.yml` (DECISIONS 2026-09-02) [V]. When `upload` is on, the folder is uploaded as a second artifact, `DistrictSync-windows-onedir` (1 day).

**What this does to the smoke script** (`scripts/ci_flet_pack_smoke.py`).
- `resolve_artifact` must pick the inner exe of a one-folder tree. 0041 already found the trap: today's candidate order matches the `dist/DistrictSync` directory first [V in 0041]. Add an explicit `--artifact onedir` kind rather than reordering `auto`.
- The offline-embed phase's pass condition today is that `~/.flet` is repopulated. For the one-folder tree it becomes the stronger one 0041 specified: the window boots with `~/.flet` absent and `FLET_CLIENT_URL` unreachable, and `~/.flet` is **still absent afterwards**, with a negative twin (remove `flet_view`, expect failure).
- The five CLI phases run unchanged against the inner exe. They already refuse to run without `DISTRICTSYNC_DATA_DIR` [V].
- A new `--installed` mode (used only in `release.yml`) points the same phases at an installed copy.

**CI time.** [I], to be measured in slice S1 and written back here: the second pack reuses the installed Python, the pip cache and the warmed Flet client, so the added cost is one PyInstaller run plus one more set of smokes on the Windows leg, on release and dispatch runs only. The release also gains one Windows job (section 7). Windows minutes bill at 2x (DSYNC-56) [V]. If S1 measures the release run more than about 15 wall-clock minutes longer, split the one-folder pack into its own parallel job.

### 3. Where `velopack.App().run()` goes, and how the CLI behaves when installed

**Build flavour, stamped like the version.** `flet-pack.yml` already stamps `src/_version.py` before packing [V]. The one-folder pack also stamps `src/_build_flavor.py` (`flavor = "installed"`); the one-file pack and every source checkout get `"portable"`. One function, `src/utils/build_flavor.py:is_installed_build()`, is the only reader. Everything Velopack-related is behind it, and `velopack` is a Windows-only, exactly pinned requirement that is imported lazily, so Linux CI can assert the module is never imported for a portable build.

**Position.** Velopack's contract is that `App().run()` is the first thing the process does, because the installer and updater start the exe with hook arguments (install, updated, obsolete, uninstall) and expect it to exit quickly [RG-R]. In `src/main.py` that is the top of `cli()`, before `_cli`'s existing argv pre-checks.

**One conflict to settle in the spike (S0), not by assumption.** `_cli` dispatches `--elevated-apply` first and that position is a security property: the elevated child must perform no filesystem side effects [V `src/main.py:441-452`]. Whether `App().run()` with ordinary arguments touches the disk, or applies a pending update package, is [N]. RosterGate's spike saw Velopack apply a pending package at the next start [RG-D]. An update applied by a process holding an elevated token is not acceptable. Recommendation: `App().run()` runs first for every invocation **except** `--elevated-apply`, `--schedule-check` and `--diagnose`, which keep their dispatch-first position and never reach Velopack, and automatic apply-at-start is switched off if the library allows it so that our own code is the only thing that applies an update. S0 confirms or changes this, and the result is pinned by a test on the dispatch order.

**CLI in the installed app.** Unchanged behaviour: the installed exe is the same program, so `DistrictSync.exe --sis ... --input ... --output ...` converts, attaches to the parent console and returns the documented exit codes. Details:
- Velopack installs to `%LOCALAPPDATA%\<packId>\` with `current\` (the app), `packages\`, `Update.exe` and a stub exe at the root [RG-D]. The scheduled task and the headless docs use `%LOCALAPPDATA%\<packId>\current\DistrictSync.exe`: the path is the same after every update because `current\` is replaced in place [I from RG-D "apply swaps the folder with a backup"]. Whether the root stub passes arguments and exit codes through is [N]; until S0 shows it does, nothing points at the stub.
- **The pack id must not be `DistrictSync`.** The profile lives at `%LOCALAPPDATA%\DistrictSync` (platformdirs, DECISIONS 2026-07) [V], and Velopack's uninstall removes its install folder [RG-D]. A pack id of `DistrictSync` would put the program inside the profile folder and an uninstall would delete settings and run history. Proposed pack id: `myBlueprint.DistrictSync`. (RosterGate has the same collision today: pack id `RosterGate` in its workflow and state folder `%LOCALAPPDATA%\RosterGate` [RG-code]. Reported to the owner as a cross-tool finding.)
- `Setup.exe --silent -- <args>` crashed Setup 1.2.161 [RG-D]. Districts are never told to pass arguments through Setup; the docs install first, then run the installed exe.
- The scheduler already registers `sys.executable` [V `src/scheduler/elevation.py:34`], so a schedule saved from the installed app points at the installed path with no scheduler change.

### 4. The update check

**When.** Two triggers, both after the work the user came for.
1. **After a scheduled run finishes** (`--source scheduled`), once output is committed, delivery is done and the run record is written. This is the trigger that matters: the set-up-once population never opens the window [V DECISIONS 2026-07-21], so a check tied only to the window would never update them.
2. **In the window**, on a background thread after the first screen is up.

A manual CLI conversion does not check: an admin running a command expects it to do one thing.

**Never blocking a conversion.** The check is not on any path before or during ETL. It cannot change the exit code: the run's exit code is computed and the run record stored before the check starts, and the check is wrapped so that any refusal or exception is logged as a bounded reason (`unreachable`, `expired`, `hash_mismatch`, ...) and nothing else. It has a total time budget (RosterGate's timeout is per read, not per download, G-073 [RG-code]; DistrictSync adds a wall-clock cap, default 5 minutes for the scheduled trigger).

**Silent.** No dialog, no toast, no restart. The Help screen gains one quiet line (installed version, last check result) so support can read the state; that is a `src/ui_flet` change and goes through the `districtsync-design` skill.

**Offline or blocked.** A refusal is a result, not an error [RG-code `check_for_update`]. The app and the nightly behave exactly as today. District firewalls that allow only the three SFTP hosts will see `unreachable` every night until they allow `mybpdesktopupdates.blob.core.windows.net:443`; the partner docs say so.

**Applying.** A verified update is staged under the profile (`<data dir>/updates/staging`), then handed to Velopack to apply after the process exits [RG-D `wait_exit_then_apply_updates(silent=True, restart=False)`]. Two DistrictSync-specific rules that RosterGate does not need:
- **Single-writer rule.** Every DistrictSync process holds a shared lock in the profile for its lifetime; apply is requested only by a process that can take the lock exclusively. If the window is open while the nightly finishes (or the reverse), the update stays staged for the next opportunity. Without this, the folder swap meets locked files.
- **Keep clear of the nightly.** The window does not request an apply within 30 minutes before the registered run time, because a nightly that starts during the folder swap may fail to start, and RosterGate saw a first start hang when the app was started during an apply [RG-D finding 5]. The scheduled trigger applies right after the nightly, the moment furthest from the next one.

**Scope guards.** The check does nothing when: the build is portable; the profile is machine scope (`is_machine_scope()`), see R1; `DISTRICTSYNC_DATA_DIR` is set (support and test seam, never a place to self-update from); or the off switch is set (question Q4).

**Egress.** The feed host is a new constant beside, not inside, `ALLOWED_SFTP_HOSTS` (`ALLOWED_UPDATE_HOSTS`, one entry), enforced at the fetch boundary; CLAUDE.md's Security section gains the line.

### 5. The signed manifest and the feed layout

One standard with RosterGate; the layout below is RosterGate's with the container changed [RG-code `tools/sign_manifest.py`, `update.py`].

```
https://mybpdesktopupdates.blob.core.windows.net/districtsync/
    manifest.json                                   (overwritten each release, uploaded LAST)
    myBlueprint.DistrictSync-3.30.0-full.nupkg      (immutable, --overwrite false)
    myBlueprint.DistrictSync-3.30.0-delta.nupkg     (immutable)
    ...earlier versions' packages stay
```

`manifest.json` is a signed envelope (`alg`, `key_id`, `payload`, `signature`) whose payload carries `schema`, `app`, `channel` (`stable`), `sequence`, `expires`, `feed_host` and `releases[]`, each with `version` and `files[]` of `name`, `sha256`, `size`, `url`. The client refuses, leaving the running version untouched: a bad or unknown-key signature; wrong `app`; expired, or valid for more than 400 days; `sequence` lower than the newest seen (replay); a `feed_host` or any file URL not on the configured host; plain http; any redirect; an oversize body; a file name with a path, a device name or a duplicate on Windows; a size or hash mismatch (staging deleted) [RG-code, each refusal has a test in RosterGate].

DistrictSync-specific points:
- `app` is `DistrictSync` and the trusted key list holds DistrictSync's own public key, so a RosterGate manifest can never be accepted by DistrictSync even though both feeds share a storage account.
- **Manifests are valid 90 days** [RG-code `VALID_DAYS`]. If DistrictSync goes more than 90 days without a release, every installed copy starts refusing the manifest as expired and logs it nightly. Either the release owner re-signs on a schedule or the validity is longer. Recommendation: keep 90 days and add a manual "re-sign current manifest" dispatch to the release workflow, because a short expiry is what bounds a stolen-manifest replay.
- **Delta chain.** `vpk` writes a listing naming every version ever built, and a client then chained through delta files that were not on the feed and failed [RG-D finding 1]. The manifest lists exactly the target full package plus the deltas that exist on the feed, and the local folder handed to Velopack carries a listing filtered to the same set. RosterGate's `sign_manifest.py` lists one release's `.nupkg` files and its hand-off to Velopack is not wired yet (G-068) [RG-code]; this filtered listing is unbuilt work in both tools.
- **Deltas need the previous full package at pack time** [I]. The release job downloads the previous version's full `.nupkg` from the container (its OIDC login can read it) before `vpk pack`. If that download fails the release still ships a full package and says so in the job summary; it never fails the release for a missing delta.
- The storage account name and feed host will appear in this public repository. They are names, not secrets.

### 6. Rollback and bad-version handling

Three different failures, three different answers.

| Failure | Answer | Where it comes from |
|---|---|---|
| The update fails **while being applied** | Velopack swaps the folder with a backup, so the old version stays | [RG-D] (log) |
| The new version **cannot start** | Local startup health check: a version that fails to come up more than twice is recorded bad, the previous full package is re-applied, the bad package is **deleted from `packages\`**, and the version is never offered again | `HealthStore` [RG-code]; the delete is required because a pending package re-applies itself at the next start [RG-D finding 3] |
| The new version **starts but is wrong** (for example it writes wrong CSVs) | Stop the spread: publish a manifest with a higher `sequence` that offers nothing newer than the last good version. Fix forward: release the fix as a higher version. | New for DistrictSync |

Notes that change DistrictSync's documented rollback (`docs/developer/release.md`, Rollback) [V]:
- **There is no downgrade through the feed.** No-downgrade is a security property (D3). An installed copy that already took a bad-but-starting version gets back to good only by a new, higher version, or by the district uninstalling and installing an older `Setup.exe` from the Release page by tag.
- **"Healthy" must not mean "the conversion succeeded".** A nightly that exits 1 because a GDE file is missing is a data problem, not a bad build. For the CLI, healthy means the preamble completed (profile pinned, logging up, bundled configs loadable); for the window, the first screen is up. The health mark is written before ETL starts.
- RosterGate's rollback is not wired to Velopack yet: the previous version is passed as the current one, "so in practice it never rolls back yet" (G-072) [RG-code]. DistrictSync cannot ship auto-update before that is real in the shared code.
- The release ticket template gains: feed manifest sequence published, previous full package retained, and the name of who can publish a stop manifest.
- DSYNC-20 (rollback rehearsal, still open) [V] should rehearse both the one-file path and the feed path.

### 7. Release workflow changes that keep the existing guarantees

`release.yml` today: `ci-green` → `build-flet` → `sign-windows` (in `release`, OIDC, sign, verify, re-smoke) → `publish-release` (tag-only). `workflow_dispatch` is the rehearsal: it builds and signs and publishes nothing [V].

Changes, all additive:

1. **`check-config`** (new, first). Lists every variable the run needs. On a tag, any missing variable stops the release **before anything is built or published** (fails closed), so a release can never publish the Release page and then fail at the feed. On a dispatch it warns and the feed steps are skipped. New variables, none secret, same names as RosterGate: `UPDATE_STORAGE_ACCOUNT`, `UPDATE_CONTAINER`, `UPDATE_FEED_HOST`, `KEYVAULT_NAME`, `MANIFEST_KEY_NAME`. DistrictSync's existing signing variables keep their current names (`SIGNING_ENDPOINT`, `SIGNING_ACCOUNT_NAME`, `SIGNING_CERTIFICATE_PROFILE_NAME`) [V]; RosterGate calls the same three `TRUSTED_SIGNING_*` [RG-code]. Renaming is an owner step and is not needed.
2. **`build-flet`** passes `windows_onedir: true`.
3. **`sign-windows`**: unchanged. The one-file exe is signed, verified and re-smoked exactly as today.
4. **`installer-windows`** (new; `environment: release`; `id-token: write` only here and in `sign-windows`; actions pinned by SHA). Steps, in this order:
   1. Download the unsigned one-folder artifact. Assert its exe is NOT already signed (the existing absence check, same reason).
   2. `azure/login` by OIDC, the same app registration and federated credential as `sign-windows`.
   3. Install `vpk` at an exact version that matches the pinned `velopack` Python package (parity test).
   4. `vpk pack --packId myBlueprint.DistrictSync --packVersion <tag> --packDir <folder> --mainExe DistrictSync.exe --azureTrustedSignFile ...`. This signs the app's executables and Setup [RG-R; `--signTemplate` signing of app files and Setup is RG-D]. The exact 1.3 option names are unrun (G-067) [RG-code].
   5. **Verify**: `signtool verify /pa` plus the timestamp assert, on `Setup.exe` and, after install, on the installed `DistrictSync.exe`, `flet.exe` and `Update.exe`. A file vpk did not sign is a failure, not a warning.
   6. **Install and re-smoke the installed app**: `Setup.exe --silent` on the runner, then the CLI phases and the window smoke against `%LOCALAPPDATA%\myBlueprint.DistrictSync\current\DistrictSync.exe` with `DISTRICTSYNC_DATA_DIR` set, then uninstall and assert the install folder is gone and the throwaway profile is not.
   7. **Sign the manifest** (algorithm per O2) and verify it with the client's own verifier and the public key baked into the build just smoked. A manifest the shipped app would refuse stops the release here.
   8. Upload `Setup.exe` as an artifact for `publish-release`.
5. **`publish-release`** (tag-only, unchanged trigger): adds `DistrictSync-Setup.exe` to the rename step, `SHA256SUMS.txt`, the `files:` list and the downloads table.
6. **`publish-feed`** (new, tag-only, after `publish-release`, in `release`): upload the `.nupkg` files with `--overwrite false`, then `manifest.json` last. Uploading the manifest is the moment installed copies start updating. Question Q3 asks whether this job should sit behind its own approval.

**Rehearsal** (`workflow_dispatch`): runs 1-4 including real signing, installs and smokes the signed installer, signs a manifest if the key variables exist, and uploads nothing to the Release or the container. Following 0041's rule that signing steps land "verified live, never dormant" [V], each workflow slice is merged only after a rehearsal run is read and quoted.

**What does not change:** `flet-pack.yml` stays secret-free with `contents: read`; no pull request trigger exists on `release.yml`; nothing signs pull request code.

### 8. First install, SmartScreen, and districts

- **No admin** is needed to install; Setup runs per user in 2 to 3 seconds [RG-D]. Registering the nightly still needs the existing per-operation UAC consent; that is unchanged.
- **Publisher.** Setup and every executable carry the `myBlueprint Corp.` signature, so Windows names the publisher [V release body].
- **SmartScreen.** Two statements disagree and the plan does not pick one. This repo's release body says a brand-new version "can still show a warning for a while" because SmartScreen builds download reputation separately [V]. Velopack's documentation says an Azure Trusted Signing certificate has instant reputation [RG-R]. Partner docs keep the cautious wording until a fresh `Setup.exe` has been downloaded on a clean machine and the result recorded (QA row).
- **Updates never meet SmartScreen** [I]: update packages are fetched by the app, not a browser, so they carry no mark-of-the-web. Only the first install is a browser download.
- **Antivirus.** The installed copy runs from `%LOCALAPPDATA%`, the location class 0041 wanted to leave. What is different from the incident shape: every executable there is signed, nothing self-extracts at launch, and the Flet client is not dropped at first run. Whether Bitdefender-class engines accept it is [N]; the CHANGELOG claims nothing about antivirus.
- **Firewall.** One new outbound host, `mybpdesktopupdates.blob.core.windows.net:443`. Not reachable means no updates and nothing else.
- **Which download to pick.** The Release page and `docs/partner/installation.md` recommend the installer for a district where one person sets up and runs the app, and the one-file exe for machine-scope and managed-service-account setups and for anyone who must not have self-updating software.

### 9. Moving an existing district from the one-file exe to the installer

1. Install `DistrictSync-Setup.exe` as the same Windows user who runs DistrictSync today. Settings, run history and the SFTP password are already in that user's profile and keyring and are used as they are; nothing is copied [V profile location and keyring service are per user, not per exe].
2. Open the installed app. The nightly task still points at the old exe. The app already reads the task's action path (`task_com` `action_path`, consumed by `schedule_probe`) [V]; when that path is not the running exe and the build is installed, Setup shows one band: the nightly still runs the older copy, with one action that re-registers it to this copy through the existing register path (same UAC consent as any schedule save).
3. Delete the old exe. Until step 2 is done the old exe keeps running the nightly, unchanged and un-updated; nothing breaks.

Not in scope: an unattended migration. A district that scripts its setup keeps the one-file exe.

### 10. Proving the installed app contacts only the feed host

Four layers, weakest first; the plan needs all four because the installed app is three processes (`DistrictSync.exe`, `flet.exe`, `Update.exe`) and only a packet capture sees the last two.

1. **Unit** (every CI run): the vendored connection-capture test, which records every connection the update check opens and requires each to be the configured host [RG-code `tests/test_update.py`].
2. **In-process, whole app** (every CI run, Linux): a `sys.addaudithook` test that boots the shell headless, runs an update check and a dry-run conversion, and fails on any `socket.connect` whose address is not loopback or the feed host. This also pins that a conversion without `--sftp` opens no connection at all.
3. **Installed app, packet capture** (`installer-windows`, every release and rehearsal): start `pktmon` on the runner, run the installed app's window smoke and one update check, stop, and require every remote address opened by the three process ids to resolve to the feed host. While the container is private the check ends in a refusal; the destination assert is the same. This step is gating only after one rehearsal has shown the capture is reliable on the hosted runner; until then it reports and the manual step below is the gate.
4. **Manual, clean machine** (release QA checklist row): install, capture, run the app and a real update, confirm every destination is the feed host. This is RosterGate's documented manual step [RG-code `docs/RELEASE.md`].

## Open decisions (owner's; a recommendation each, none decided here)

### O1. How DistrictSync gets the updater code

A fact that shapes every option: RosterGate's updater is not app-neutral today. `services/update.py` imports `rostergate.content` (the signed envelope, the Ed25519 verifier, the version helpers), checks `app == "RosterGate"`, names RosterGate in its refusal messages and reads `ROSTERGATE_STATE_DIR` [RG-code]. A second fact: DistrictSync is a **public** repository [V CLAUDE.md] and RosterGate is private, so any option that puts RosterGate code here publishes it.

| Option | What it costs | What it buys |
|---|---|---|
| **A. Vendor at a pinned RosterGate commit** (proposed): files copied into `src/updater_core/`, a recorded SHA-256 list, a CI check that fails when a vendored file differs from the list, a sync script | RosterGate must first move the updater into an app-neutral folder with no `rostergate.*` imports and the app name, key list and state folder as arguments (work in RosterGate). Every fix is two changes: RosterGate, then a sync PR here. DistrictSync's CI cannot read the private repo, so CI proves "not edited locally", and "matches upstream at the pinned commit" is proved only when the sync script runs on a machine with access. The vendored files publish that part of RosterGate. DistrictSync's lint, type and coverage gates need a declared stance on vendored code, and RosterGate's updater tests must be vendored too or the 80% gate and the security claim rest on tests that do not run here. | No new repository. Cannot drift silently. Each tool releases on its own schedule against a known commit. |
| B. A shared package in its own repository, pinned by commit hash | A third repository with its own CI, release and access rules; a private dependency in a public repo's CI needs a token, which this repo's workflows deliberately avoid; the owner is sceptical of a new repository | One copy of the code; the usual dependency tooling |
| C. Each tool implements a written manifest spec, with shared signed test vectors | Two implementations of security-critical code to review and keep right; behaviour can differ where the spec is silent | No code crosses repos; nothing private is published |
| D. Git submodule or subtree | A private submodule breaks every clone and CI run of a public repo; a subtree is option A with weaker checks | Nothing over A |

**Recommendation: A**, on three conditions: (1) RosterGate first extracts an app-neutral `updater_core` (update check, envelope, verifier, health store) and finishes the two unbuilt parts both tools need, the Velopack hand-off and a real rollback (G-068, G-072); (2) the vendored set includes its tests and signed test vectors, and they run in DistrictSync CI; (3) the owner confirms that publishing those files in this public repository is acceptable. If (3) is a no, the choice becomes C, not B.

### O2. Manifest signature algorithm

Ed25519 today in RosterGate, with the 32-byte seed read from a Key Vault **secret** into the release job's memory, because Key Vault keys cannot be Ed25519 [RG-code `docs/RELEASE.md`, read by RosterGate 2026-11-12]. Proposed: ECDSA P-256 (ES256) signed inside the vault with `az keyvault key sign`, so the private key never leaves it.

**Recommendation: ECDSA P-256 in the vault, decided before either tool ships a trusted key.** The public key is baked into every installed copy, so changing the algorithm after the first installer ships needs a bridging release that trusts both keys; changing it now costs one verifier. Costs to name: a P-256 verifier in the shared code, written to the same standard RosterGate used for Ed25519 (standard library only, checked against published vectors); the envelope's `alg` value is checked, never trusted to choose a weaker path; Key Vault returns the signature as raw `r||s` rather than DER [I, to confirm in the slice]; the release identity needs Key Vault Crypto User on that one key. DistrictSync already ships the `cryptography` package through `paramiko` [V `requirements.txt`], so it could verify with a vetted library, but one standard means one verifier in the shared code; if the owner prefers the vetted library for both tools, that is a RosterGate decision (it rejected the dependency for size) and is raised as question Q6.

### O3. Azure role grants for the release identity

Not in place; waiting on the org admin. What to ask for, scoped as narrowly as each service allows:

| Grant | Scope | Needed for |
|---|---|---|
| Artifact Signing Certificate Profile Signer | signing account `data-sync`, profile `districtsync-public-trust` | already held by DistrictSync's release identity (it signs every release today) [V]; no change |
| Storage Blob Data Contributor | container `districtsync` only | uploading packages and the manifest; reading the previous full package for deltas |
| Key Vault Crypto User (ES256) or Key Vault Secrets User (Ed25519 seed) | the one manifest key or secret | signing the manifest (depends on O2) |

**Recommendation:** grant these to the app registration DistrictSync already uses (`github-districtsync-signing`), whose federated credential is already scoped to this repo's `release` environment [V `release.yml` comment], rather than creating a second identity. Give RosterGate its own identity and its own container grant, so neither tool's release job can write the other's feed. Nothing in slices S0-S5 or S6a waits on these grants; S6b and the first real release do.

## Affected files

| Path | Change |
|---|---|
| `.github/workflows/flet-pack.yml` | `windows_onedir` input; second pack (`-D`); client extract into `_internal/flet_view`; flavour stamp; one-folder smokes; second artifact |
| `.github/workflows/release.yml` | `check-config`; `installer-windows`; `publish-release` gains Setup; `publish-feed` |
| `.github/workflows/flet-verify.yml` | dispatch passes `windows_onedir: true`; pull requests unchanged |
| `scripts/ci_flet_pack_smoke.py` | `--artifact onedir`, `--installed`, offline proof for the bundled client |
| `scripts/flet_pack_args.py` (new) | the one hidden-import list both packs read |
| `scripts/sign_manifest.py`, `scripts/sync_updater_core.py`, `scripts/check_vendored.py` (new) | manifest signing; vendoring sync; hash check |
| `src/main.py` | `App().run()` position; post-run update trigger for scheduled runs |
| `src/utils/build_flavor.py`, `src/_build_flavor.py` (stamped, git-ignored) | installed vs portable |
| `src/updater_core/` (new, vendored) + `VENDORED.sha256` | the shared updater (per O1) |
| `src/update/` (new) | DistrictSync's adapter: triggers, lock, scope guards, Velopack hand-off, health wiring |
| `src/ui_flet/launcher.py` | `FLET_VIEW_PATH` for the installed build; window trigger; health mark |
| `src/ui_flet/screens/` (Help, Setup) | version and last-check line; "nightly still runs the older copy" band |
| `src/config/app_config.py` | `update_` fields (last check, off switch if Q4 is yes) |
| `src/utils/validators.py` | `ALLOWED_UPDATE_HOSTS` |
| `requirements.txt` | `velopack==<exact> ; sys_platform == "win32"` |
| `tests/` | per slice, below |
| `docs/DECISIONS.md`, `docs/INVARIANTS.md`, `CLAUDE.md`, `docs/developer/release.md`, `qa-checklist.md`, `architecture-notes.md`, new `docs/developer/updates.md`, `docs/partner/installation.md`, `faq.md`, `troubleshooting.md`, `headless-sftp-setup.md`, `managed-service-accounts.md`, `CHANGELOG.md` | each rides the slice that makes it true |

## Risks & mitigations

Output CSVs: no ETL change in any slice; the SD74 snapshot gates every slice.

| # | Risk | Source | Mitigation |
|---|---|---|---|
| R1 | **Machine scope and managed service accounts.** A per-user install lives in one user's `%LOCALAPPDATA%`. A nightly that runs as a gMSA or another account cannot be assumed to read or execute it, and cannot write it to apply an update. SD60's first gMSA rollout failed on exactly a program-folder permission [V DSYNC-43, DSYNC-48]. | [I] from D2 + plan 0049 | The updater is off in machine scope and the installer is documented as not for those setups; they keep the one-file exe (which is why D1 matters). A machine-wide installed format is a later plan, not smuggled into this one. |
| R2 | A silent update changes what every district's nightly sends, the night after release. Today "districts install on their own schedule". | [I] | Q3: a separate approval before the manifest is published, and the manual QA pass moves to the signed installer from the rehearsal run. Output-contract changes already need importer confirmation before merge [V]. |
| R3 | A pending package re-applies itself at the next start. | [RG-D] finding 3 | Rollback deletes the package and records the version bad (section 6); a test pins both. |
| R4 | No automatic rollback in Velopack: a build that exits at start stayed installed. | [RG-D] | The health check is ours; auto-update is not switched on until it is wired to a real Velopack rollback (G-072). |
| R5 | The feed listing must match the files on the feed or a delta chain fails. | [RG-D] finding 1 | Manifest and local listing are filtered to files that exist; the release job verifies every listed URL before uploading the manifest. |
| R6 | One-folder only; and the app folder is about 76 MB, full package 63 MB, Setup 71 MB for a toy app. | [RG-D] finding 2 | Sizes for DistrictSync are measured in S1 and added to the size summary. The 21 KB delta is a toy number (only the version string changed); real deltas are unmeasured [RG-D risk 4]. |
| R7 | Starting the app during an apply hung a first start once. | [RG-D] finding 5 | Single-writer lock and the keep-clear window (section 4); the nightly path is tested with an apply in flight in S0. |
| R8 | `flet pack` failed once on a temp-file delete, probably antivirus. | [RG-D] finding 6 | One bounded retry in the pack step, logged; never a silent retry loop. |
| R9 | `vpk` 1.3 changed signing and the spike ran 1.2.161; the workflow's signing options are unrun. Signing inside `vpk` from Windows PowerShell 5.1 could not see the certificate store. | G-067; [RG-D] | S0 runs `vpk` at the version to be pinned; the signing step is rehearsed live before its slice lands; `pwsh` is used explicitly. |
| R10 | Velopack is one third-party project (MIT; 48 open issues when read). | [RG-D] risk 5 | Exact pins on `vpk` and the Python package with a parity test; because Velopack never touches the network (D3), replacing it later changes the installer, not the trust model. |
| R11 | Whether Velopack or Windows checks Authenticode on an update package is unknown; the Velopack feed carries hashes and no signature. | [RG-D] | This is why the manifest layer exists. The plan never relies on Velopack verifying anything. |
| R12 | Manifest key custody, rotation and revocation are not designed. | G-070 | Trusted keys are a list with `key_id`; the plan ships with two slots (current, next) so rotation is a release, not a redesign. Custody follows O2. Revocation of a version is the stop manifest; revocation of a key is a release that drops it. |
| R13 | Anyone with write access can push a tag on any commit. | G-073 | Already partly covered here: `ci-green` requires a green CI run on the tagged commit [V]. Recommend required reviewers on the `release` environment and a tag protection rule (owner step, Q8). |
| R14 | The client read path is not open; a private container makes every check `unavailable`. | G-074; D5 | Nothing breaks. The first release that turns updates on is gated on the owner's container step, checked by `check-config` reading the manifest URL anonymously. |
| R15 | An installer published before the trusted key is baked in can never update itself. | [I] | `DistrictSync-Setup.exe` is not attached to a public Release until the build carries the production public key and the updater (slice order below). |
| R16 | Two Windows downloads increase support questions. | DSYNC-16 open question [V] | One recommendation sentence on the Release page and in the install guide (section 8); `--diagnose` prints the build flavour. |
| R17 | `App().run()` in the elevated child, or in `--diagnose`. | [N] | Section 3; settled in S0; pinned by a dispatch-order test. |
| R18 | The elevated schedule helper runs our own exe from a user-writable folder (0041 row 15). The installed copy is also in a user-writable folder. | [V] 0041 | Same accepted trade as the one-file exe, same mitigation (signature shown on the UAC prompt). Named so the DECISIONS entry does not claim the installer closes it. |

## Test strategy

Every slice: targeted tests locally, then CI's own result read and quoted (land gate, DECISIONS 2026-07-30). Windows and macOS legs run on main, dispatch and release, not on pull requests (DSYNC-56), so a slice that touches the Windows build is not landed until a dispatch run of the affected workflow is quoted in the pull request. No vacuous greens: every "did not connect", "did not import", "was not signed" assertion has a positive twin.

## Decomposition (slices, in build order; each is one ticket, one branch, one pull request)

- [ ] **S0. Spike on the real app, evidence only.** Build DistrictSync one-folder with the bundled client, pack it unsigned with `vpk` at the version to be pinned, install it on a clean Windows user, and answer every [N] above: `App().run()` side effects with ordinary arguments and in the three dispatch-first modes; whether apply-at-start can be turned off; stub exe arguments and exit codes; install folder name for the pack id; a nightly started during an apply; apply while a second process holds the lock; sizes and a real delta between two real tags. *Test:* a transcript committed beside this plan and the [N] labels here replaced by results. Lands no product code. Lands complete because its output is facts.
- [ ] **S1. One-folder Windows build in CI, unsigned, unpublished.** `flet-pack.yml` input, second pack, bundled client, flavour stamp, shared hidden-import list, smoke script `onedir` kind and the new offline proof. *Tests:* resolver units; offline proof with its negative twin; parity test that both packs read one hidden-import list; a dispatch run on all three OSes quoted, with measured added minutes and sizes written back into section 2. Nothing new is published; the release's five assets are byte-for-byte the same jobs as before.
- [ ] **S2. Installed-build identity and Velopack startup.** `build_flavor`, the `velopack` pin, `App().run()` at the position S0 settled, `--diagnose` prints the flavour. No update check. *Tests:* a portable build never imports `velopack` (sys.modules assert, runs on Linux); dispatch order pin for `--elevated-apply`, `--schedule-check`, `--diagnose`; `vpk`/`velopack` version parity.
- [ ] **S3. Vendored updater core** (after O1 and O2 are decided and RosterGate has an app-neutral core). Files, hash list, `check_vendored.py` wired into `ci.yml` and pre-commit, sync script, vendored tests and vectors. *Tests:* the vendored suite; the hash check fails on a one-byte edit (negative twin) and passes clean; a DistrictSync manifest is accepted and a RosterGate-app manifest is refused.
- [ ] **S4. Update check and staging, apply still off.** `src/update/` adapter, both triggers, time budget, lock, scope guards, `ALLOWED_UPDATE_HOSTS`, `AppConfig` fields, Help line. Stages a verified update and stops. *Tests:* layers 1 and 2 of section 10; the exit code and run record of a scheduled run are identical with the feed unreachable, refusing, slow past the budget, and raising; machine scope, `DISTRICTSYNC_DATA_DIR`, portable and off-switch each make zero connections, with a positive twin that an eligible build makes exactly one host's connections; UI slice definition of done from the design skill.
- [ ] **S5. Apply, health check, rollback, bad-version record.** Velopack hand-off with the filtered listing, single-writer and keep-clear rules, health mark before ETL, rollback that deletes the bad package. *Tests:* unit tests on the health store and the listing filter; an end-to-end workflow (`updater-e2e.yml`, dispatch and release only, unsigned test-flavour build that trusts a test key and a loopback feed, never the release build): install N, update to N+1 by delta, refuse each tampered feed, install a build that exits at start and see it rolled back and never offered again. The release build has a test that it cannot be pointed at loopback or a test key.
- [ ] **S6a. Release workflow: pack, sign, verify, install, re-smoke; `Setup.exe` joins the Release.** `check-config`, `installer-windows` steps 1-6 and 8, `publish-release` additions, release docs and ticket template. Needs no new Azure grant. *Tests:* workflow-shape tests in the style of RosterGate's `tests/test_release_workflow.py` (only two jobs hold `id-token: write`, both in `release`; no pull request trigger; publish jobs are tag-gated; every action pinned by SHA); a rehearsal run quoted showing pre-sign unsigned, signed, verified with timestamp, installed, CLI and window smokes green on the installed signed copy, nothing published. Per R15 this slice lands only after S3-S5, so the first public `Setup.exe` can update itself.
- [ ] **S6b. Manifest signing and feed publish.** Steps 7 and `publish-feed`, the re-sign dispatch, layer 3 of section 10. Blocked on O2, O3 and the manifest key existing. *Tests:* the release job verifies its own manifest with the shipped verifier; rehearsal quoted with the feed steps skipped-with-warning when variables are missing and fail-closed proven by a tag-shaped dry run of `check-config`.
- [ ] **S7. Districts: migration band, partner docs, QA rows.** Section 9's band, `installation.md`, `faq.md`, `troubleshooting.md`, `headless-sftp-setup.md`, `managed-service-accounts.md`, QA checklist rows (clean-machine install, SmartScreen result recorded, migration from one-file, uninstall keeps the profile, layer 4 capture). *Tests:* doc-copy parity registrations for any quoted app copy; band logic units; the QA pass recorded on the release ticket.
- [ ] **S8. First release with updates on.** Owner steps done (container public-read, grants, key, variables), then a normal release per `release.md`. *Test:* a district-shaped machine installed from the previous release's `Setup.exe` takes the new version overnight with the nightly's result unchanged; recorded on the release ticket. DSYNC-20's rollback rehearsal runs against the feed before this.

Slices S0-S2 can start on approval of this plan. S3 waits on O1, O2 and RosterGate. S6b and S8 wait on O3 and the owner's Azure steps.

## Needs the owner

- **Q1 (O1).** Vendoring as proposed, including publishing those RosterGate files in this public repository? If not, option C.
- **Q2 (O2).** ECDSA P-256 in the vault, for both tools, before any trusted key ships?
- **Q3.** Should publishing the manifest (the moment every installed district starts updating) be a separate approval after the Release page is live, so a release can be checked before it spreads?
- **Q4.** Should a district be able to turn updates off (a documented config setting, no UI prompt)? Recommendation: yes; some district IT policies forbid self-updating software, and the alternative for them is the one-file exe.
- **Q5.** Machine-scope and managed-service-account districts stay on the one-file exe for now (R1). Acceptable, or is a machine-wide installed format wanted as a follow-on plan?
- **Q6.** Verifier: standard-library code in the shared module (RosterGate's choice) or the `cryptography` package DistrictSync already ships?
- **Q7.** Flet client inside the install folder (`FLET_VIEW_PATH`, 0041 Goal 4) as the standard for both tools?
- **Q8.** Required reviewers on the `release` environment and a tag protection rule (R13)?
- **Q9 (O3).** Grants to the existing `github-districtsync-signing` identity as tabled, and the manifest key name.
- **Owner steps, none done by this plan:** make the `districtsync` container public-read; create the manifest key; role grants; set the five GitHub variables; record the D1-D6 decisions in `docs/DECISIONS.md` (drafted with slice S1's pull request for the owner to confirm).

## What was verified and what was inferred

- **Read here [V]:** this repo's `CLAUDE.md`, `release.yml`, `flet-pack.yml`, `flet-verify.yml`, `ci.yml` outline, `docs/developer/release.md`, `jira-automation.md`, plan 0041, `docs/DECISIONS.md` (2026-08-05 entries, DSYNC-56, DSYNC-57), `src/main.py` dispatch preamble, `ALLOWED_SFTP_HOSTS`, the smoke script's interface, Jira DSYNC-16.
- **Read in RosterGate at `e375646` [RG-code]:** `docs/RELEASE.md`, `docs/GAPS.md`, `DECISIONS.md` 2026-11-12, `services/update.py`, `updater/__init__.py`, `tools/sign_manifest.py`, `tools/build_app.py`, `release.yml`. Its spike README was read from branch `m1b-updater-spike`.
- **Not run by me:** nothing was built, packed, installed, signed or sent to Azure in this session. Every Velopack behaviour here is RosterGate's evidence or documentation, labelled as such.
- **Inferred [I]:** the stable `current\` exe path across updates; the previous-full-package need for deltas; updates not meeting SmartScreen; CI minute estimates; Key Vault's signature encoding; that a gMSA cannot use a per-user install.
- **Unverified [N]:** listed in S0.

---

## Review

Not yet reviewed. Filled after the owner approves the direction.

## Spec

Per slice, after Review passes. S0 needs no spec beyond its question list above.
