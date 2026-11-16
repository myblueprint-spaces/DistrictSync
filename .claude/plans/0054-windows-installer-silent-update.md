# 0054 — Windows installer with silent, signed auto-update (Velopack)

- **Status:** Approved to proceed by the owner 2026-11-15 ("can proceed with the work"), with the answers recorded under "Owner answers 2026-11-15". First target: a real signed `DistrictSync-Setup.exe` from a rehearsal run that the owner installs like a customer, with the existing release untouched.
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
| D7 | The manifest signature is ECDSA P-256, signed inside Key Vault `mybp-desktop-signing`; the private key never leaves the vault. This replaces RosterGate's current Ed25519 envelope for both tools. | Owner, relayed 2026-11-13 |

**These decisions supersede parts of plan 0041 and must be recorded in `docs/DECISIONS.md` at approval** (with `Supersedes:` lines): 0041 Approach B's Inno Setup and per-machine `Program Files` install; 0041's rejection of a per-user install ("re-arms profile-execution patterns"); 0041's non-goal "auto-update". 0041's Goal 4 (ship the Flet client inside the install tree, never consult `~/.flet`) is NOT superseded and is carried into this plan.

## Goals / Non-goals

**Goals**

1. A signed `DistrictSync-Setup.exe` on every GitHub Release, beside every asset published today.
2. An installed copy that updates itself silently from the `districtsync` feed, without ever delaying, blocking or changing the result of a conversion.
3. Nothing is installed that was not signed by us twice: Authenticode on every executable, and our manifest signature over every update file.
4. A bad update that cannot start is rolled back locally and never offered again; a bad update that starts is stopped from spreading by one manifest publish.
5. The release workflow keeps every existing guarantee (D4) and gains a rehearsal that proves the installed, signed app without publishing anything.
6. The updater code is the same code RosterGate runs, by one shared package (O1, decided 2026-11-16).

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

**Build.** A second `flet pack` with `-D` (one-folder) and the same hidden-import list, **in its own job** in `flet-pack.yml` (`pack-windows-onedir`), not inside the existing Windows leg. Owner priority 2026-11-15: the existing one-file exe must not be put at risk, so a failure in the one-folder build must not be able to fail or delay the job that builds the exe districts use today. `flet pack --onedir` exists natively in flet-cli 0.85.3 (0041 Approach B) [V in 0041, RG-D for `flet pack -D`]. The hidden-import list must not be written twice: it moves into one place both invocations read (a small `scripts/flet_pack_args.py` or a YAML anchor), with a parity test, because "never duplicate config" and the "literal copied into CI needs a parity test" rule both apply [V CLAUDE.md].

**The Flet client goes inside the folder (0041 Goal 4, carried).** One-folder alone does not stop the first-launch extraction into `~/.flet`; 0041's Round-1 review verified that against flet_desktop 0.85.3 [V in 0041]. The Windows one-folder job extracts the client into `_internal/flet_view/` and the frozen launcher sets `FLET_VIEW_PATH` to it. Then `flet.exe` is a file in our tree that `vpk pack` signs, and the quarantine-brick mode is unreachable for installed copies. RosterGate instead collects Flet whole (`--collect-all flet --collect-all flet_desktop`, `tools/build_app.py`) and has not run that build (G-069) [RG-code]; whether its folder still drops into `~/.flet` is [N]. This is a place the two tools differ today and one standard should be picked (question Q7).

**What this does to `flet-pack.yml`.** One new boolean input, `windows_onedir` (default false). `release.yml` and a manual `flet-verify` dispatch set it; a pull request does not, and pull requests pack Linux only anyway (DSYNC-56) [V], so **pull request CI time does not change**. The workflow keeps `contents: read` and takes no secrets: the one-folder tree is built and smoked unsigned there and signed only in `release.yml` (DECISIONS 2026-09-02) [V]. When `upload` is on, the folder is uploaded as a second artifact, `DistrictSync-windows-onedir` (1 day).

**What this does to the smoke script** (`scripts/ci_flet_pack_smoke.py`).
- `resolve_artifact` must pick the inner exe of a one-folder tree. 0041 already found the trap: today's candidate order matches the `dist/DistrictSync` directory first [V in 0041]. Add an explicit `--artifact onedir` kind rather than reordering `auto`.
- The offline-embed phase's pass condition today is that `~/.flet` is repopulated. For the one-folder tree it becomes the stronger one 0041 specified: the window boots with `~/.flet` absent and `FLET_CLIENT_URL` unreachable, and `~/.flet` is **still absent afterwards**, with a negative twin (remove `flet_view`, expect failure).
- The five CLI phases run unchanged against the inner exe. They already refuse to run without `DISTRICTSYNC_DATA_DIR` [V].
- A new `--installed` mode (used only in `release.yml`) points the same phases at an installed copy.

**CI time.** [I], to be measured in slice S1 and written back here: the separate job repeats the Python install and Flet warm-up, so it costs about one more Windows pack-and-smoke leg, in parallel, on release and dispatch runs only. The release also gains one Windows job (section 7). Windows minutes bill at 2x (DSYNC-56) [V].

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
   7. **Sign the manifest** (ES256 in the vault, D7) and verify it with the client's own verifier and the public key baked into the build just smoked. A manifest the shipped app would refuse stops the release here.
   8. Upload `Setup.exe` as an artifact for `publish-release`.
5. **`publish-release`** (tag-only, unchanged trigger): adds `DistrictSync-Setup.exe` to the rename step, `SHA256SUMS.txt`, the `files:` list and the downloads table.
6. **`publish-feed`** (new, tag-only, after `publish-release`, in `release`): upload the `.nupkg` files with `--overwrite false`, then `manifest.json` last. Uploading the manifest is the moment installed copies start updating. Question Q3 asks whether this job should sit behind its own approval.

**Rehearsal** (`workflow_dispatch`): runs 1-4 including real signing, installs and smokes the signed installer, signs a manifest if the key variables exist, and uploads nothing to the Release or the container. The signed `Setup.exe` is kept as a run artifact (1 day) so the owner can download and install it like a customer. A dispatch input `draft_release` (default off) attaches it to a **draft** GitHub Release instead: a draft is invisible to the public and does not change `latest`, and it gives the same browser download a customer gets. A version tag is never used for testing, because a tag publishes a real Release that becomes `latest`. Following 0041's rule that signing steps land "verified live, never dormant" [V], each workflow slice is merged only after a rehearsal run is read and quoted.

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

1. **Unit** (every CI run): the shared package's connection-capture test (run in its own repository's CI), which records every connection the update check opens and requires each to be the configured host [RG-code `tests/test_update.py`].
2. **In-process, whole app** (every CI run, Linux): a `sys.addaudithook` test that boots the shell headless, runs an update check and a dry-run conversion, and fails on any `socket.connect` whose address is not loopback or the feed host. This also pins that a conversion without `--sftp` opens no connection at all.
3. **Installed app, packet capture** (`installer-windows`, every release and rehearsal): start `pktmon` on the runner, run the installed app's window smoke and one update check, stop, and require every remote address opened by the three process ids to resolve to the feed host. While the container is private the check ends in a refusal; the destination assert is the same. This step is gating only after one rehearsal has shown the capture is reliable on the hosted runner; until then it reports and the manual step below is the gate.
4. **Manual, clean machine** (release QA checklist row): install, capture, run the app and a real update, confirm every destination is the feed host. This is RosterGate's documented manual step [RG-code `docs/RELEASE.md`].

## Open decisions (O1 and O3 are the owner's, with a recommendation each; O2 was decided on 2026-11-13 and is kept here for what it costs)

### O1. How DistrictSync gets the updater code: DECIDED 2026-11-16, one shared private package

**Owner ruling 2026-11-16:** one shared package, `myb_desktop_updater`, in its own **private** repository ("no public we have to secure"). Not a vendored copy and not two implementations. DistrictSync depends on it pinned to an exact commit with a hash lock. This plan therefore no longer has a vendored folder, a recorded hash list, a "not edited locally" CI check or a sync script.

**What the package owns (written once, by RosterGate, which is making its `updater/` package app-neutral in place now):** the signed-manifest check (ES256 through `cryptography`), the host-locked fetcher, staging, the hand-off to Velopack with the filtered listing, the health record and bad-version list, rollback with its own retained copy of the last good package (S0 finding 10), and the update off switch as an argument. The app passes an `AppIdentity`.

**What DistrictSync keeps (wiring only):** its identity (app name, pack id, trusted public keys, feed host, state folder under the profile), when to check (after a scheduled run and in the window), what "healthy" means (start-up completed, never "the conversion succeeded"), the scope guards (portable build, machine scope, `DISTRICTSYNC_DATA_DIR`), the keep-clear-of-the-nightly rule, and where the off switch is stored (`AppConfig`).

**What private costs.** Stated so the owner sees them; nothing below has been created.

1. **CI needs read access to the private repository.** The workflow's own `GITHUB_TOKEN` cannot read another repository. Options:
   - *Read-only deploy key on the package repo, one per consuming repo* (likely form): an SSH key stored as an Actions secret in DistrictSync, read-only, scoped to that one repository, not tied to a person. Cost: one secret per consumer to rotate.
   - *GitHub App installation token*: short-lived, nothing long-lived stored except the App's key; the cleanest security, the most setup.
   - *Fine-grained personal access token*: quick, but tied to one person's account and expiry.
   - *A private package feed* (GitHub Packages or Azure Artifacts): the package is published as a wheel and read with a token, or for Azure Artifacts through the OIDC login the release job already uses, which needs no stored secret but only works in jobs that sign in to Azure.
2. **DistrictSync is a public repository today, so the secret must stay out of fork pull requests.** How:
   - GitHub does not give Actions secrets to `pull_request` runs from forks, and this repo never uses `pull_request_target` (`flet-verify.yml` comment) [V]. That rule stays.
   - Consequence: a fork pull request (and a Dependabot pull request, which also gets no Actions secrets) **cannot install the package**. So the package must not be in `requirements.txt`. It goes in its own file (`requirements-updater.txt`), installed only by jobs that hold the secret: the push to `main`, same-repository pull requests, and the release.
   - The wiring imports the package lazily and only in the installed build; tests of the wiring that need the real package use `pytest.importorskip`, so a fork run is green without it and a `main` run proves it. The no-vacuous-green rule applies: the `main` run must fail if those tests were skipped.
   - `flet-pack.yml` is secret-free by rule (RC4) because it packs pull request code [V]. The one-folder (installed) pack needs the package. So that job takes the read key as an optional `workflow_call` secret that **only `release.yml` passes**; the pull request caller cannot pass it, and the one-file build never needs it. This narrows RC4's wording from "takes no secrets" to "takes no secret on a pull request" and needs a DECISIONS entry.
   - Once the DistrictSync repository is private (MCRM-262), the fork concern goes away. The read-access need does not.
3. **"Pinned to an exact commit with a hash lock" needs a decision on form.** pip cannot hash-check a `git+https://...@<sha>` requirement; `--require-hashes` only works for files. Two honest forms: (a) pin the git commit SHA only (the SHA identifies the content; no pip hash), or (b) the package repo builds a wheel per commit or tag, the consumer downloads it with its read access and installs it with `--hash=sha256:...`. (b) is the real hash lock and is what "hash lock" should mean; it needs the package repo to publish wheels.
4. **Developers and Dependabot.** Anyone building the installed flavour locally needs access to the private repo. Dependabot cannot bump a private dependency without its own configured secret, so the pin is raised by hand in a ticketed change, with the package's changelog read.
5. **A third repository** has its own CI, its own release steps and its own review. It needs a home on a Jira board and the same way of work (differences row 13).
6. **`cryptography` pin.** RosterGate pins `cryptography==50.0.2`. DistrictSync gets `cryptography` only through `paramiko` and does not pin it [V `requirements.txt`]. The installed build pins the same version so both tools verify with the same library version.
7. **The built installer still contains the package's code** (as compiled Python inside the install folder), and anyone can download the installer. Private protects the source and its history, not the shipped behaviour.

### O2. Manifest signature algorithm: DECIDED (D7), ECDSA P-256 signed inside the vault

Decided by the owner on 2026-11-13. What the decision means for the build, none of which exists yet in either tool [RG-code: RosterGate `main` still signs Ed25519 from a vault secret, and its `docs/RELEASE.md` says "Nothing has been built for option 2"]:

- The release job signs the manifest digest with `az keyvault key sign --algorithm ES256`; the app holds only the public key.
- The shared code needs a P-256 verifier. RosterGate wrote its Ed25519 verifier in the standard library and checked it against published vectors; the same standard applies, or the vetted library (question Q6). DistrictSync already ships `cryptography` through `paramiko` [V `requirements.txt`].
- The envelope's `alg` value is checked against the one expected algorithm; it never selects a weaker path.
- Key Vault returns the signature as raw `r||s`, not DER [I, to confirm in slice S6b].
- The release identity needs Key Vault Crypto User on that one key (O3).
- Because the public key is baked into every installed copy, the key must exist and its public half must be in the build before the first public `Setup.exe` (R15).

### O3. Azure role grants for the release identity

Not in place; waiting on the org admin. What to ask for, scoped as narrowly as each service allows:

| Grant | Scope | Needed for |
|---|---|---|
| Artifact Signing Certificate Profile Signer | signing account `data-sync`, profile `districtsync-public-trust` | already held by DistrictSync's release identity (it signs every release today) [V]; no change |
| Storage Blob Data Contributor | container `districtsync` only | uploading packages and the manifest; reading the previous full package for deltas |
| Key Vault Crypto User | the one manifest key in `mybp-desktop-signing` | signing the manifest (ES256, D7) |

**Recommendation:** grant these to the app registration DistrictSync already uses (`github-districtsync-signing`), whose federated credential is already scoped to this repo's `release` environment [V `release.yml` comment], rather than creating a second identity. Give RosterGate its own identity and its own container grant, so neither tool's release job can write the other's feed. Nothing in slices S0-S5 or S6a waits on these grants; S6b and the first real release do.

## Affected files

| Path | Change |
|---|---|
| `.github/workflows/flet-pack.yml` | `windows_onedir` input; second pack (`-D`); client extract into `_internal/flet_view`; flavour stamp; one-folder smokes; second artifact |
| `.github/workflows/release.yml` | `check-config`; `installer-windows`; `publish-release` gains Setup; `publish-feed` |
| `.github/workflows/flet-verify.yml` | dispatch passes `windows_onedir: true`; pull requests unchanged |
| `scripts/ci_flet_pack_smoke.py` | `--artifact onedir`, `--installed`, offline proof for the bundled client |
| `scripts/flet_pack_args.py` (new) | the one hidden-import list both packs read |
| `scripts/sign_manifest.py` (new, or the shared package's own signing tool if it ships one) | manifest signing in the release job |
| `src/main.py` | `App().run()` position; post-run update trigger for scheduled runs |
| `src/utils/build_flavor.py`, `src/_build_flavor.py` (stamped, git-ignored) | installed vs portable |
| `requirements-updater.txt` (new) | `myb_desktop_updater` pinned (exact commit, hash lock per O1 point 3) and `cryptography` pinned; installed only where CI has read access |
| `src/update/` (new) | DistrictSync's wiring only: `AppIdentity`, triggers, scope guards, what healthy means, keep-clear rule. No manifest, fetch, staging, hand-off or rollback code of its own |
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
| R12 | Manifest key custody, rotation and revocation are not designed. | G-070 | Trusted keys are a list with `key_id`; the plan ships with two slots (current, next) so rotation is a release, not a redesign. Custody is the vault (D7). Revocation of a version is the stop manifest; revocation of a key is a release that drops it. |
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
- [ ] **S3. Consume the shared package.** Waits on `myb_desktop_updater` existing in its private repo with the app-neutral `AppIdentity` API, and on CI read access (O1). Adds `requirements-updater.txt`, the read-access step in the jobs allowed to hold it, DistrictSync's `AppIdentity` (app name `DistrictSync`, pack id, feed host, trusted key slots, state folder under the profile) and the lazy import behind the build flavour. No update behaviour yet. *Tests:* a portable build and a fork-shaped run never import the package (sys.modules assert); on `main` the package imports and a manifest signed with the package's test key for `DistrictSync` is accepted while one for another app is refused; the wiring tests are proven not skipped on `main`; pin parity between `requirements-updater.txt` and what the workflow installs; a workflow-shape test that no pull request path can receive the read secret.
- [ ] **S4. Wire the check: when, and when not.** The package does the check, verification and staging; DistrictSync supplies both triggers (after a scheduled run, in the window), the wall-clock budget, the scope guards, `ALLOWED_UPDATE_HOSTS`, the `update_enabled` setting passed to the package as its off switch, and the Help line. Apply stays off. *Tests:* layer 2 of section 10 (layer 1 ships with the package); the exit code and run record of a scheduled run are identical with the feed unreachable, refusing, slow past the budget, and raising; machine scope, `DISTRICTSYNC_DATA_DIR`, portable and off-switch each make zero connections, with a positive twin that an eligible build connects to exactly the feed host; UI slice definition of done from the design skill.
- [ ] **S5. Wire apply and health.** The package does the Velopack hand-off, the retained last-good copy, rollback and the bad-version record. DistrictSync supplies: `App().run()` settings (apply-at-start off), the healthy mark written before ETL starts, the single-writer and keep-clear-of-the-nightly rules, and the call sites. *Tests:* units on DistrictSync's healthy rule (a conversion that exits 1 never counts as a failed start) and the keep-clear window; an end-to-end workflow (`updater-e2e.yml`, dispatch and release only, unsigned test-flavour build that trusts the package's test key and a loopback feed, never the release build): install N, update to N+1 by delta, refuse a tampered feed, install a build that exits at start and see it rolled back and never offered again. The release build has a test that it cannot be pointed at loopback or a test key. If the package cannot yet do hand-off or rollback when this slice starts, the slice waits; DistrictSync does not write its own.
- [ ] **S6a. Release workflow: pack, sign, verify, install, re-smoke; `Setup.exe` joins the Release.** `check-config`, `installer-windows` steps 1-6 and 8, `publish-release` additions, release docs and ticket template. Needs no new Azure grant. *Tests:* workflow-shape tests in the style of RosterGate's `tests/test_release_workflow.py` (only two jobs hold `id-token: write`, both in `release`; no pull request trigger; publish jobs are tag-gated; every action pinned by SHA); a rehearsal run quoted showing pre-sign unsigned, signed, verified with timestamp, installed, CLI and window smokes green on the installed signed copy, nothing published. Per R15 this slice lands only after S3-S5, so the first public `Setup.exe` can update itself.
- [ ] **S6b. Manifest signing and feed publish.** Steps 7 and `publish-feed`, the re-sign dispatch, layer 3 of section 10. Blocked on O3 and the manifest key existing. *Tests:* the release job verifies its own manifest with the shipped verifier; rehearsal quoted with the feed steps skipped-with-warning when variables are missing and fail-closed proven by a tag-shaped dry run of `check-config`.
- [ ] **S7. Districts: migration band, partner docs, QA rows.** Section 9's band, `installation.md`, `faq.md`, `troubleshooting.md`, `headless-sftp-setup.md`, `managed-service-accounts.md`, QA checklist rows (clean-machine install, SmartScreen result recorded, migration from one-file, uninstall keeps the profile, layer 4 capture). *Tests:* doc-copy parity registrations for any quoted app copy; band logic units; the QA pass recorded on the release ticket.
- [ ] **S8. First release with updates on.** Owner steps done (container public-read, grants, key, variables), then a normal release per `release.md`. *Test:* a district-shaped machine installed from the previous release's `Setup.exe` takes the new version overnight with the nightly's result unchanged; recorded on the release ticket. DSYNC-20's rollback rehearsal runs against the feed before this.

Slices S1-S2 can start on the owner's go-ahead (S0 is done). S3 to S5 wait on the shared package and CI read access (O1). S6b and S8 wait on O3 and the owner's Azure steps.

## Differences from RosterGate (owner decision 2026-11-16: standardise)

Owner, 2026-11-16: "yes standardise the tools and processes these are our only 2 win exe no point having unnecessary differences". RosterGate column read from its `main` at `258a7d6` on 2026-11-16 (read-only). **Need** column: **Owner** = needs an owner decision; **Align** = alignment work only, no decision needed. **Changes** = which tool has to change to reach the standard.

| # | Area | DistrictSync today / planned | RosterGate today | Proposed single standard | Is the difference necessary? | Need | Changes |
|---|---|---|---|---|---|---|---|
| 1 | Velopack version | Nothing shipped. Spike ran `vpk` and `velopack` 1.2.161. | **Aligned 2026-11-16:** workflow installs `vpk` 1.2.161 exactly (read at `b8d739c`). | `vpk` and the `velopack` package pinned to 1.2.161 in both, raised together. | No. | Closed for RosterGate; DistrictSync pins the same in S2 | DistrictSync |
| 2 | Pack id and install folder | Proposed `myBlueprint.DistrictSync`. Must not be `DistrictSync`: the profile is `%LOCALAPPDATA%\DistrictSync` and uninstall deletes the install folder. | `--packId RosterGate`; state folder defaults to `%LOCALAPPDATA%\RosterGate`: the same collision. | `myBlueprint.<Tool>` for the pack id; `--packTitle <Tool>` (it names the launcher exe); app data stays in `%LOCALAPPDATA%\<Tool>`. | No. Permanent once an installer ships. | **Owner** | Both |
| 3 | Authenticode signing call | One-file exe signed by `azure/trusted-signing-action` today. Installer planned through `vpk pack --azureTrustedSignFile`. Variables `SIGNING_ENDPOINT`, `SIGNING_ACCOUNT_NAME`, `SIGNING_CERTIFICATE_PROFILE_NAME`. | **Aligned 2026-11-16:** writes a signing metadata file and passes its path; `--runtime win-x64`. Still unrun against Azure. Variables `TRUSTED_SIGNING_ENDPOINT`, `_ACCOUNT`, `_PROFILE`. | The same `vpk pack` call (metadata file, `--runtime win-x64`) and the same variable names in both. Then: assert unsigned, sign, verify signature and timestamp, re-smoke. | Partly. DistrictSync keeps the action for the one-file exe, which RosterGate does not ship. | Align (renaming DistrictSync's GitHub variables is an owner step) | DistrictSync copies the call in S6a |
| 4 | Manifest format and expiry | Planned: RosterGate's envelope and payload, `app` = `DistrictSync`, 90 days. | Envelope `alg`/`key_id`/`payload`/`signature`, ES256; payload `schema` 1, `app`, `channel`, `sequence`, `expires`, `feed_host`, `releases[]`; `VALID_DAYS` 90, client cap 400 days. | RosterGate's format as written, plus one shared "re-sign the current manifest" workflow run so a tool with no release for 90 days does not strand installed copies. | No. Only the `app` value and the key differ, by design. | Align; the re-sign run and the 90 days are an **Owner** confirm | DistrictSync adopts; both add the re-sign run |
| 5 | Verifier | Decided 2026-11-15: `cryptography`. Shipped through `paramiko`, not pinned. | **Aligned 2026-11-16:** `cryptography==50.0.2` pinned; the updater is "standard library and cryptography only". | `cryptography`, same pinned version in both, used inside the shared package. | No. | Closed; DistrictSync pins 50.0.2 for the installed build | DistrictSync |
| 6 | Feed layout and host name | Container `districtsync`; host not chosen. Recommended a department-owned name, because the host is baked into every installed copy. | Container `rostergate`; `UPDATE_FEED_HOST` = `mybpdesktopupdates.blob.core.windows.net`; flat layout, `manifest.json` at the container root. | One host name for both tools, one container (path) per tool, the same flat layout, packages immutable, manifest uploaded last. | The container differs by design. The host must not differ. | **Owner** (host name, and Front Door or a CDN if it is a custom name) | Both |
| 7 | Updater code | None; will depend on the shared package. | `src/rostergate/updater/` being made app-neutral in place (`AppIdentity` argument), to become the package. | **Decided 2026-11-16:** one shared package `myb_desktop_updater` in its own private repo, pinned by exact commit with a hash lock in each tool. | No for the package. Yes for each tool's wiring (rows 9, 10). | Decided. New questions under O1 (access mechanism, hash-lock form) | RosterGate extracts it; both consume it |
| 8 | Rollback | Wiring only (what healthy means). | `HealthStore` and `startup_health` exist; Velopack rollback and the retained last-good copy are being added to the shared package. | Written once in the shared package: health record, bad-version list, retained last-good package, delete the bad package, re-apply. | The mechanism: no. What counts as a healthy start: yes (row 10). | Align | RosterGate (in the package) |
| 9 | Update off switch | Decided yes; stored as `update_enabled` in `AppConfig` and passed in. | Going into the shared package as an argument. | The package takes the switch; each tool stores it in its own settings. | No. | Align | RosterGate (in the package) |
| 10 | When to check, and what "healthy" means | After a scheduled run and in the window; never before or during a conversion; healthy = start-up completed, not "conversion succeeded". | At window start; healthy = first screen up. No CLI, no scheduled run. | Shared rule: never block the user's work; apply only after exit. The triggers stay per tool. | **Yes.** DistrictSync runs unattended every night and RosterGate is only ever opened by a person. | Align | Neither |
| 11 | Build | `flet pack` (one-file today; `flet pack -D` planned), Flet client to be bundled inside the install folder, pinned `requirements*.txt`. | `tools/build_app.py`: PyInstaller `--onedir --collect-all flet`, never run (its G-069); PyInstaller not in its lock; `--require-hashes` lock file. | One documented one-folder recipe with the Flet client inside the install folder (decided: duplicated per tool, not shared). Hash-locked requirements in both. | The recipe: no. The script: yes, the apps differ. | Align | Both (DistrictSync adds hash-locked pins; RosterGate adopts the bundled client) |
| 12 | CI and release workflow shape | `ci.yml` (PRs on Linux only), reusable `flet-pack.yml`, `release.yml`: `ci-green`, build, `sign-windows`, tag-only publish; a manual run signs and publishes nothing; three OSes. | `ci.yml` one `gates` job; `release.yml`: `check-config` (fails closed on a missing variable), `build` (runs the gates again), `sign-and-publish`; manual run has a `rehearse` input; Windows only. | Same job names and order for the Windows installer in both: `check-config`, build unsigned, sign in the `release` environment, verify, install and re-smoke, publish feed last. Same rehearsal rule. DistrictSync keeps its extra one-file, macOS and Linux jobs. | Partly. DistrictSync's other platforms are a real difference. One real choice: DistrictSync's rehearsal signs for real, RosterGate's signs nothing. | **Owner** (does a rehearsal sign?) | Both |
| 13 | Way of work and PR process | Jira ticket before a branch; `DSYNC-<n>` in branch, commit and PR title; automation moves the ticket; numbered plans in `.claude/plans/`; PR template; land only when CI's result is read. | No Jira key convention, no PR template; milestone branches (`m8-updater`) merged to `main`; `docs/PLAN.md` milestones; `DECISIONS.md` one-liners; "do not push without the owner's say". | DistrictSync's written process for both: ticket first, key in branch, commit and PR, plan before code, PR with CI read. RosterGate needs a Jira project or a home in an existing one. | No. | **Owner** (RosterGate's Jira project and key) | RosterGate |
| 14 | Decision record | `docs/DECISIONS.md`, dated entries with `Supersedes:`. | `DECISIONS.md` at the root, dated one-liners with the rejected alternative. | Same location and one entry format; an entry that affects both tools is written in both with the same date and wording. | No. | Align | Either; pick one format |
| 15 | Data hygiene gates | Email scan with an exact allowlist; real-data rules in `CLAUDE.md`. | Data-hygiene scan: no email outside `example.invalid`, no `.csv` outside `tests/`. | Each keeps its own scan; both keep "synthetic data only". | **Yes.** DistrictSync ships real district config and fixtures by design; the rules cannot be the same. | Align | Neither |
| 16 | Network proof | Planned four layers (unit capture, in-process audit hook, installed-app packet capture, manual clean machine). | Unit connection-capture test; installed-app capture is a manual step (its G-071). | The unit test ships with the shared core; the same installed-app capture step in both release workflows. | No. | Align | Both |

**Rows needing the owner:** 2 (pack id convention), 6 (feed host name), 12 (does a rehearsal sign for real), 13 (RosterGate's Jira project, and now the package repo's), and a confirmation on 4 (90 days plus a re-sign run). Row 7 is decided (shared private package) and raises the O1 questions on CI access and hash-lock form. Rows 1, 3 and 5 are closed on RosterGate's side.

**Necessary differences, kept on purpose:** 10 (when to check), 15 (data rules), and the parts of 11 and 12 that come from DistrictSync shipping a one-file exe, macOS and Linux.

## Owner answers 2026-11-15

| # | Question | Answer | Effect on the plan |
|---|---|---|---|
| Q1 | Updater code (O1) | 2026-11-15: "hide DistrictSync behind the new integration.myblueprint.org". **2026-11-16: decided, one shared private package `myb_desktop_updater`.** | O1 rewritten; S3 to S5 are wiring only. |
| Q2 | Signature algorithm | ECDSA P-256 in the vault (D7, 2026-11-13) | Decided. |
| Q3 | Separate approval before the manifest is published | Not now: "currently lets get it working." | No gate in the first build. Stays as risk R2 and is revisited before S8 (first release with updates on). |
| Q4 | Off switch | Yes | `update_enabled` in `AppConfig` (config only, no prompt), in S4. |
| Q5 | Service-account and machine-scope districts | Make the installer work in every scenario over time; make it robust. | R1 becomes a workstream, not a permanent exclusion. See "Gaps to full coverage". The first build still ships with the updater off in machine scope. |
| Q6 | Verifier | **Decided 2026-11-15: the `cryptography` library for both tools.** | The shared updater verifies ES256 with `cryptography`; RosterGate gains the dependency. |
| Q7 | Flet client location | "Probably fine"; asked whether sharing it is over-engineering. | Inside the install folder, **duplicated in each tool, not shared**: it is one build step and a few launcher lines, and each tool's build differs. Only the updater is shared. |
| Q8 | Release protection | Later, after the migration | R13 stays open and is listed for S8. |
| Q9 | Role grants (O3) | The owner will try to elevate; otherwise ask Doug. | The ask is drafted in this plan's pull request. |
| - | Proceed? | "Can proceed with the work." | S0 starts on this ticket (DSYNC-16 is "evaluate", and names the one-folder build as its first step). |

**Recommendation on Q6 (verifier).** Use the `cryptography` package in the shared code. A hand-written P-256 verifier is more error-prone than the Ed25519 one RosterGate wrote (more special cases, plus encoding and malleability checks that a vetted library already gets right), and this is the one check that decides whether code runs on every district machine. Cost: RosterGate gains a compiled dependency of a few MB that it rejected for size; DistrictSync already ships it through `paramiko`, so it costs DistrictSync nothing. The other choice (standard-library code checked against published vectors) keeps RosterGate small and puts the correctness burden on our own tests.

**Recommendation on Q7 (Flet client).** Inside the install folder, for both tools. Every executable that runs then sits in one signed folder and nothing is extracted into the user profile at first launch, which is the behaviour Bitdefender quarantined in 2026-08. Cost: one build step and one environment variable set by the launcher; the folder is no larger, because the client is already embedded as an archive today.

### Gaps to full coverage (Q5)

What the per-user installer does not cover yet, in the order I would close them. Each is its own slice after the first installer works.

1. **A nightly that runs as a different account** (a stored-password service account or a gMSA). The program sits in the installing user's `%LOCALAPPDATA%`; the other account needs read and execute there, and cannot apply an update there. Whether Velopack can install to a chosen folder or machine-wide (and how it then updates without admin) is [N] and is a named question for S0.
2. **Machine scope (plan 0049).** The data folder is shared and trust-checked; the updater must not write there, and whoever applies an update must be one declared account, not whichever user opens the window.
3. **A district that never opens the window and whose nightly account differs from the installer.** No process that can write the install folder ever runs, so nothing would ever update. Needs either a machine-wide install with an updater that runs as the nightly account, or an honest "this setup does not update itself" line in Help and `--diagnose`.
4. **Several Windows users on one server**, each installing their own copy against one scheduled task.
5. **Locked-down networks**: only the three SFTP hosts allowed, or TLS inspection that breaks the feed connection. The app reports it and the docs carry the firewall line; nothing else can be done from our side.
6. **Uninstall and reinstall** must never remove the profile, the keyring entry or the scheduled task. Covered for the profile by the pack id (section 3); the task is left alone and the docs say so.
7. **Migration from the one-file exe without opening the window** (a scripted district). Not covered; they keep the one-file exe.

## Still needed from the owner

1. **Answered in part 2026-11-15 (MCRM-262, out of scope here, the owner's next work):** partners will download from the signed-in partner site and the DistrictSync repository becomes private afterwards. Still open for this plan: the app cannot sign in to that site, so the update feed must stay readable without sign-in on its own address, which means anyone who knows the address can download the signed update packages (binaries only, no source, no district data). Original question, kept for the feed-host part: **what "behind integration.myblueprint.org" covers.** Three readings, each changing something different: (a) districts download from that site, not from the GitHub Release page; (b) the update feed is served from that host, not from `mybpdesktopupdates.blob.core.windows.net`; (c) the GitHub repository becomes private. Recommendation: (b) at least, from the first installer. The feed host is baked into every installed copy and is the one thing that cannot be changed later without a bridging release, so it should be a name the department owns (for example `updates.integration.myblueprint.org`) pointing at the storage account. Azure Blob does not serve https on a custom domain by itself, so that needs Azure Front Door or a CDN in front (an Azure change for the owner or Doug). If (c): the fork pull request concern in O1 disappears, and the public `releases/latest/download/...` links in partner docs and emails stop working, so (a) must be live first.
3. **Pack id** `myBlueprint.DistrictSync` (install folder `%LOCALAPPDATA%\myBlueprint.DistrictSync`). It cannot be changed after the first installer ships.
4. **Owner or Doug steps, none done by this plan:** role grants and the manifest key (the drafted ask); the five GitHub variables; the feed's public read path; recording D1-D7 in `docs/DECISIONS.md` (drafted with slice S1's pull request for the owner to confirm).

## S0 spike findings (2026-11-15, run on the owner's Windows 11 machine, not elevated)

What was run: the real app at this branch's commit, built one-folder with `flet pack -D` through a spike entry script (Velopack start-up call, then the unchanged `cli()`), packed unsigned with `vpk` 1.2.161 and the `velopack` 1.2.161 Python package (the versions RosterGate's spike left on this machine; 1.3 is still unrun), installed with `Setup.exe --silent` under a throwaway pack id and `DISTRICTSYNC_DATA_DIR`, then uninstalled. Nothing was signed. The spike scripts and logs are in the session scratch folder, not in the repo; the lines quoted below are from those logs. Label **[S0]** = demonstrated here.

| # | Question | Result [S0] |
|---|---|---|
| 1 | Does the real app build one-folder? | Yes. Pack 119 s. Folder 162.5 MB (2,473 files); full package 101.2 MB; `Setup.exe` 108.7 MB; install needs 318 MB free. (Today's one-file exe is about 96 MB.) |
| 2 | Real delta size | 3.29.1 to 3.29.2 with only the version stamp changed: **912 KB** ("4 patched, 2466 unchanged"). A release with real code changes is still unmeasured. |
| 3 | Install | `Setup.exe --silent`, not elevated, exit 0, 12 s (10 s extracting files). Installs to `%LOCALAPPDATA%\<packId>\` with `current\`, `packages\`, `Update.exe` and a stub exe. Runs the app once with `--veloapp-install <version>`; `App().run()` handles it and exits (0.7 s). |
| 4 | `App().run()` with ordinary arguments | Returns in 20 to 30 ms, then the CLI runs as normal. Outside an install it returns in 1 ms and prints `VelopackApp: Error loading manager/locator: NotInstalled(...)` to the console: the portable build must not call it at all (section 3's build flavour), or that line reaches every CLI user. |
| 5 | CLI and exit codes from `current\DistrictSync.exe` | Unchanged: `--version` 0; unknown `--sis` 1; conflicting flags 2; a full SD74 fixture conversion wrote `csv/` and the `acsv_` zip, exit 0. |
| 6 | The stub exe at the install root | **Unusable for the CLI.** It starts the app through `Update.exe start` without waiting: no output, exit code always 0. Scheduled tasks and docs must use `current\DistrictSync.exe`. The stub is named from `--packTitle`, so the title must be `DistrictSync`. |
| 7 | Update from a local folder | Works: `UpdateManager(<local folder>)` found 3.29.2 with one delta, rebuilt the full package in 13 s, and `wait_exit_then_apply_updates(silent=True, restart=False)` applied it about 6 s after the process exited. The run's exit code was unchanged (0). The exe path is the same after the update. Hooks `--veloapp-obsolete` and `--veloapp-updated` each start the app once. |
| 8 | Apply while another DistrictSync process is running | **Fails safe.** With the window open, the updater retried the folder swap 10 times ("Access is denied"), then gave up: `Unable to start the update, because one or more running processes prevented it`. The window was not killed and the installed version stayed 3.29.1. So the single-writer rule (section 4) is about not wasting a nightly's apply, not about preventing damage. |
| 9 | Can apply-at-start be switched off? | The API exists: `App().set_auto_apply_on_startup(False)`. Not yet proven with a pending package (item 8 left one pending; proving it needs one more build). Pinned by the S5 end-to-end test. |
| 10 | Is the previous version's package kept? | **No.** After an update (and even after the failed apply in item 8) `packages\` holds only the newest full package. Section 6's rollback cannot re-apply "the previous full package" from there: the app must keep its own copy of the last good full package under the profile, or download it again from the feed. |
| 11 | Uninstall | `Update.exe uninstall --silent` removed the install folder, the Start Menu shortcut and the uninstall registry key. The profile folder was untouched. |
| 12 | Flet client | The one-folder app still ran `flet.exe` from `%USERPROFILE%\.flet\client\...`, as plan 0041 said. The bundled-client step (0041 Goal 4) is still needed and was not built in this spike. |
| 13 | Other install locations | `Setup.exe` has `--installto <DIR>`, and `vpk pack` has `--msi` ("machine-wide bootstrap package") and `--instLocation`. These are the candidates for the service-account and machine-scope gaps; none was run. |
| 14 | `vpk` details for the workflow | `vpk pack` warns `No architecture specified with --runtime, defaulting to x86`: pass `--runtime win-x64`. Default shortcuts are Desktop and Start Menu: set `--shortcuts` deliberately. `--azureTrustedSignFile` takes a **path to a `metadata.json` file** in 1.2.161; RosterGate's workflow passes `endpoint|account|profile` as one string for 1.3, unrun. `vpk` checks for its own updates unless `--skip-updates` is passed. Velopack writes its own logs to `%LOCALAPPDATA%\velopack\`. |

**Still unverified after S0:** `vpk` 1.3 and real Trusted Signing through it; the elevated schedule child and `--diagnose` (skipped by design in the spike entry, not exercised); apply-at-start off with a pending package; a nightly that starts during a folder swap; `--installto` and `--msi`; a delta between two real releases.

**Changes this forces in the plan above:** section 3's stub caveat is now a rule (item 6); section 6's "previous full package is re-applied" needs our own retained copy (item 10); R7's risk is smaller than written (item 8); the portable build must never reach `App().run()` (item 4).

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
